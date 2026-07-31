"""
OpenAgent Context Engine  (Target Architecture v3 §22 / Architecture v1 §16)

Assembles relevant code snippets from the RepositoryIndex into a
token-budget-aware prompt context block.

Pipeline:
  user_request → intent keywords → symbol lookup → file snippets →
  keyword-relevance scoring → budget trimming → formatted prompt block

Design decisions:
- No external embedding model required (uses stdlib difflib SequenceMatcher
  for relevance scoring). Extension point: swap `_score_relevance` with a
  sentence-transformer scorer once the BGE model is available.
- Token budget enforced by character count (1 token ≈ 4 chars) — fast and
  dependency-free. Replace with a proper tokenizer when available.
- Never sends full files. Always sends the smallest slice of context that
  satisfies the budget.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.repository_index import RepositoryIndex

# 1 token ≈ 4 characters (GPT-family heuristic; good enough for budget trimming)
_CHARS_PER_TOKEN = 4

# Default context budget: 30_000 chars ≈ 7_500 tokens, well within 8K context window
_DEFAULT_BUDGET_CHARS = 30_000

# Max lines to include per file snippet
_MAX_SNIPPET_LINES = 80


@dataclass
class CodeSnippet:
    filepath: str
    start_line: int
    end_line: int
    content: str
    relevance_score: float = 0.0
    symbol_name: str = ""


@dataclass
class ContextAssembly:
    """Result of context assembly — ready to be injected into the model prompt."""
    snippets: list[CodeSnippet] = field(default_factory=list)
    total_chars: int = 0
    total_tokens_est: int = 0
    budget_chars: int = _DEFAULT_BUDGET_CHARS
    truncated: bool = False
    summary: str = ""


class ContextEngine:
    """
    Assembles a relevant, token-budget-constrained code context block from
    the workspace repository index.

    Extension points:
    - Override `_score_relevance()` to plug in a vector embedding scorer.
    - Override `_extract_snippet()` to use tree-sitter range queries.
    """

    def __init__(
        self,
        repository_index: "RepositoryIndex",
        budget_chars: int = _DEFAULT_BUDGET_CHARS,
    ) -> None:
        self._index  = repository_index
        self._budget = budget_chars

    # ── Public API ────────────────────────────────────────────────────────────

    def assemble(self, user_request: str, workspace_root: str | Path) -> ContextAssembly:
        """
        Main entry point. Takes a user request, returns a ContextAssembly
        with the most relevant code snippets within the token budget.
        """
        workspace_root = Path(workspace_root)
        keywords = self._extract_keywords(user_request)

        # 1. Find candidate files via symbol lookup and import graph
        candidates: list[CodeSnippet] = []
        seen_files: set[str] = set()

        for keyword in keywords:
            for sym in self._index.find_symbol(keyword):
                if sym.file not in seen_files:
                    seen_files.add(sym.file)
                    snippet = self._extract_snippet(
                        Path(sym.file), sym.line, symbol_name=sym.name
                    )
                    if snippet:
                        candidates.append(snippet)

        # 2. Score and rank by relevance to the user request
        for snippet in candidates:
            snippet.relevance_score = self._score_relevance(
                user_request, snippet.content
            )
        candidates.sort(key=lambda s: s.relevance_score, reverse=True)

        # 3. Fill budget greedily (highest relevance first)
        result = ContextAssembly(budget_chars=self._budget)
        for snippet in candidates:
            if result.total_chars + len(snippet.content) > self._budget:
                result.truncated = True
                break
            result.snippets.append(snippet)
            result.total_chars += len(snippet.content)

        result.total_tokens_est = result.total_chars // _CHARS_PER_TOKEN
        result.summary = self._build_summary(result, user_request)
        return result

    def format_prompt_block(self, assembly: ContextAssembly) -> str:
        """
        Formats the assembled context into a markdown prompt block
        ready to prepend to the user message.
        """
        if not assembly.snippets:
            return ""

        lines = ["## Relevant Code Context\n"]
        for s in assembly.snippets:
            lines.append(f"### `{s.filepath}` (lines {s.start_line}–{s.end_line})\n")
            lines.append(f"```python\n{s.content}\n```\n")

        if assembly.truncated:
            lines.append(
                f"\n_Context truncated to fit token budget "
                f"(~{assembly.total_tokens_est} tokens used of "
                f"{assembly.budget_chars // _CHARS_PER_TOKEN} available)._\n"
            )
        return "\n".join(lines)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _extract_keywords(self, user_request: str) -> list[str]:
        """
        Extracts candidate symbol names from a user request using a simple
        heuristic: CamelCase words, snake_case words, and quoted identifiers.
        Extension: replace with an intent classifier / NER model.
        """
        # Match CamelCase, snake_case identifiers, and quoted strings
        pattern = r'`([^`]+)`|"([^"]+)"|([A-Z][a-zA-Z0-9]+)|(\b[a-z_][a-z0-9_]{2,}\b)'
        matches = re.findall(pattern, user_request)
        # Flatten and de-duplicate while preserving order
        seen: set[str] = set()
        keywords: list[str] = []
        for groups in matches:
            for g in groups:
                if g and g not in seen:
                    seen.add(g)
                    keywords.append(g)
        return keywords[:20]  # Cap at 20 keywords to avoid over-fetching

    def _extract_snippet(
        self, filepath: Path, anchor_line: int, symbol_name: str = ""
    ) -> CodeSnippet | None:
        """
        Extracts a code window around anchor_line from a source file.
        Returns None if the file is unreadable.
        """
        try:
            lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None

        half = _MAX_SNIPPET_LINES // 2
        start = max(0, anchor_line - half - 1)
        end   = min(len(lines), anchor_line + half)
        content = "\n".join(lines[start:end])

        return CodeSnippet(
            filepath     = str(filepath),
            start_line   = start + 1,
            end_line     = end,
            content      = content,
            symbol_name  = symbol_name,
        )

    def _score_relevance(self, query: str, content: str) -> float:
        """
        Keyword-overlap relevance score using difflib SequenceMatcher.
        Returns a float in [0.0, 1.0].

        Extension point: replace with cosine similarity over BGE embeddings.
        """
        if not content:
            return 0.0
        return difflib.SequenceMatcher(
            None,
            query.lower()[:500],   # Limit for speed
            content.lower()[:2000],
        ).ratio()

    def _build_summary(self, assembly: ContextAssembly, request: str) -> str:
        n = len(assembly.snippets)
        t = assembly.total_tokens_est
        return (
            f"Assembled {n} snippet(s) (~{t} tokens) for: "
            f'"{request[:80]}{"..." if len(request) > 80 else ""}"'
        )
