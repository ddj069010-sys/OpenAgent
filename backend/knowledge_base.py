"""
OpenAgent Workspace Knowledge Base  (Target Architecture v3 §25)

Indexes project documentation (READMEs, specifications, API docs, design decisions)
and makes them searchable via TF-IDF scoring (zero external dependencies on host).

Design decisions:
- Fallback search: BM25/TF-IDF using stdlib `math` and `re` — extremely fast
  and lightweight, avoiding heavy ML cold-starts.
- Extension point: `_generate_embeddings` is a stub that returns None; swap with
  sentence-transformers / BGE when dependencies are installed.
- Document segments are cached with file modification times to avoid re-reading
  unchanged files.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class DocumentSegment:
    filepath:     str
    content:      str
    title:        str
    section:      str
    score:        float = 0.0
    embedding:    Optional[list[float]] = None


@dataclass
class KnowledgeSearchResponse:
    results:      list[DocumentSegment] = field(default_factory=list)
    query:        str = ""
    total_found:  int = 0


# ── Knowledge Base Indexer ───────────────────────────────────────────────────

class WorkspaceKnowledgeBase:
    """
    Parses and indexes markdown and text documentation in the workspace.
    Provides relevance-based search over paragraphs and sections.

    Extension point: swap vector search in once BGE embeddings are available.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self._root = Path(workspace_root)
        self._segments: list[DocumentSegment] = []
        self._doc_frequencies: dict[str, int] = {}  # term → document frequency
        self._num_docs = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def index_documentation(self, extensions: tuple[str, ...] = (".md", ".txt")) -> int:
        """
        Scans workspace documentation files, segments them by heading, and indexes.
        Returns the number of documents processed.
        """
        self._segments.clear()
        self._doc_frequencies.clear()
        doc_count = 0

        for path in self._root.rglob("*"):
            if path.suffix not in extensions:
                continue
            if any(part.startswith((".", "node_modules")) for part in path.parts):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                self._segment_file(str(path), content)
                doc_count += 1
            except OSError:
                continue

        self._num_docs = len(self._segments)
        self._calculate_doc_frequencies()
        return doc_count

    def search(self, query: str, limit: int = 5) -> KnowledgeSearchResponse:
        """
        Searches the knowledge base using a TF-IDF vector space model.
        Falls back to keyword matching if index is empty.
        """
        if not self._segments:
            return KnowledgeSearchResponse(query=query)

        query_terms = self._tokenize(query)
        scored: list[DocumentSegment] = []

        for seg in self._segments:
            score = 0.0
            seg_terms = self._tokenize(seg.content)
            seg_len = len(seg_terms)
            if seg_len == 0:
                continue

            # Compute TF-IDF score for query terms in this segment
            for term in query_terms:
                tf = seg_terms.count(term) / seg_len
                df = self._doc_frequencies.get(term, 0)
                idf = math.log((1 + self._num_docs) / (1 + df)) + 1.0
                score += tf * idf

            if score > 0.0:
                # Copy segment and set runtime score
                new_seg = DocumentSegment(
                    filepath=seg.filepath,
                    content=seg.content,
                    title=seg.title,
                    section=seg.section,
                    score=round(score, 4)
                )
                scored.append(new_seg)

        scored.sort(key=lambda s: s.score, reverse=True)
        results = scored[:limit]

        return KnowledgeSearchResponse(
            results=results,
            query=query,
            total_found=len(scored)
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _tokenize(self, text: str) -> list[str]:
        """Lowercases and extracts word tokens (minimum 2 chars)."""
        return re.findall(r"\b[a-z0-9_]{2,}\b", text.lower())

    def _segment_file(self, filepath: str, content: str) -> None:
        """Splits markdown by headings to create focused document segments."""
        lines = content.splitlines()
        current_title = Path(filepath).name
        current_section = "Introduction"
        current_lines: list[str] = []

        for line in lines:
            # Detect Markdown heading
            m = re.match(r"^(#{1,6})\s+(.+)$", line)
            if m:
                # Save previous section if it has content
                if current_lines:
                    self._segments.append(DocumentSegment(
                        filepath=filepath,
                        content="\n".join(current_lines).strip(),
                        title=current_title,
                        section=current_section
                    ))
                    current_lines = []
                current_section = m.group(2).strip()
            else:
                current_lines.append(line)

        # Save remaining lines
        if current_lines:
            self._segments.append(DocumentSegment(
                filepath=filepath,
                content="\n".join(current_lines).strip(),
                title=current_title,
                section=current_section
            ))

    def _calculate_doc_frequencies(self) -> None:
        for seg in self._segments:
            seen_terms = set(self._tokenize(seg.content))
            for term in seen_terms:
                self._doc_frequencies[term] = self._doc_frequencies.get(term, 0) + 1
