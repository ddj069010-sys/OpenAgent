"""
OpenAgent Repository Intelligence — stdlib AST Index  (Target Architecture v3 §22)

Builds a lightweight symbol + import + dependency graph for the active workspace
using Python's stdlib `ast` module (zero external dependencies on the RTX 3050 host).

Tree-sitter is the long-term target (see architecture gap analysis); this module
provides the same *interface* so the switch to tree-sitter is a one-line container
rebinding, not a rewrite.

Supports:
  - Python symbol extraction (functions, classes, methods)
  - Import graph (who imports what)
  - Call graph approximation (simple name-call detection)
  - File hash caching (never re-parses unchanged content)
  - Incremental updates (only re-index changed files)
"""

from __future__ import annotations

import ast
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class SymbolInfo:
    name:     str
    kind:     str          # "function" | "class" | "method" | "import"
    file:     str
    line:     int
    parent:   Optional[str] = None   # enclosing class for methods


@dataclass
class FileIndex:
    path:        str
    content_hash: str
    symbols:     list[SymbolInfo]  = field(default_factory=list)
    imports:     list[str]         = field(default_factory=list)
    calls:       list[str]         = field(default_factory=list)
    indexed_at:  float             = field(default_factory=time.time)


# ── AST visitor ──────────────────────────────────────────────────────────────

class _SymbolExtractor(ast.NodeVisitor):
    """Walks a Python AST and extracts symbols, imports, and calls."""

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.symbols:  list[SymbolInfo] = []
        self.imports:  list[str]        = []
        self.calls:    list[str]        = []
        self._class_stack: list[str]    = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.symbols.append(SymbolInfo(
            name=node.name, kind="class",
            file=self.filepath, line=node.lineno
        ))
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind   = "method" if self._class_stack else "function"
        parent = self._class_stack[-1] if self._class_stack else None
        self.symbols.append(SymbolInfo(
            name=node.name, kind=kind,
            file=self.filepath, line=node.lineno,
            parent=parent
        ))
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # same treatment

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.append(node.module)

    def visit_Call(self, node: ast.Call) -> None:
        # Capture simple name calls and attribute calls (obj.method)
        if isinstance(node.func, ast.Name):
            self.calls.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.calls.append(node.func.attr)
        self.generic_visit(node)


# ── Repository Indexer ───────────────────────────────────────────────────────

class RepositoryIndex:
    """
    Builds and maintains a lightweight symbol/import/call graph for a workspace.

    Extension point: swap `_index_file` with a tree-sitter implementation
    by overriding or subclassing — the graph API (`symbols_in`, `imports_of`,
    `callers_of`) is identical.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self._root    = Path(workspace_root)
        self._index:  dict[str, FileIndex] = {}   # path → FileIndex
        self._hashes: dict[str, str]       = {}   # path → content hash

    # ── Public API ───────────────────────────────────────────────────────────

    def index_workspace(self, extensions: tuple[str, ...] = (".py",)) -> int:
        """
        Incrementally indexes all source files in the workspace.
        Returns the number of files (re-)indexed.
        """
        indexed = 0
        for path in self._root.rglob("*"):
            if path.suffix not in extensions:
                continue
            if any(part.startswith((".","__pycache__","node_modules")) for part in path.parts):
                continue
            if self._needs_reindex(path):
                self._index_file(path)
                indexed += 1
        return indexed

    def symbols_in(self, filepath: str) -> list[SymbolInfo]:
        """Returns all symbols defined in a file."""
        fi = self._index.get(filepath)
        return fi.symbols if fi else []

    def imports_of(self, filepath: str) -> list[str]:
        """Returns all modules imported by a file."""
        fi = self._index.get(filepath)
        return fi.imports if fi else []

    def find_symbol(self, name: str) -> list[SymbolInfo]:
        """Finds all symbols with a given name across the workspace."""
        results = []
        for fi in self._index.values():
            results.extend(s for s in fi.symbols if s.name == name)
        return results

    def callers_of(self, name: str) -> list[str]:
        """Returns file paths that call a given function/method name."""
        return [
            fi.path for fi in self._index.values()
            if name in fi.calls
        ]

    def summary(self) -> dict:
        """Returns a compact workspace summary for context assembly."""
        total_symbols = sum(len(fi.symbols) for fi in self._index.values())
        return {
            "indexed_files":  len(self._index),
            "total_symbols":  total_symbols,
            "root":           str(self._root),
        }

    def export_graph(self) -> dict:
        """Exports the full index as a serialisable dict (for context engine use)."""
        return {
            path: {
                "symbols": [vars(s) for s in fi.symbols],
                "imports": fi.imports,
                "calls":   fi.calls,
            }
            for path, fi in self._index.items()
        }

    # ── Internal ─────────────────────────────────────────────────────────────

    def _content_hash(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _needs_reindex(self, path: Path) -> bool:
        key = str(path)
        try:
            current = self._content_hash(path)
        except OSError:
            return False
        if self._hashes.get(key) == current:
            return False
        self._hashes[key] = current
        return True

    def _index_file(self, path: Path) -> None:
        """Parses a single Python file and stores its index entry."""
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree   = ast.parse(source, filename=str(path))
        except SyntaxError:
            return  # Skip unparseable files silently

        extractor = _SymbolExtractor(filepath=str(path))
        extractor.visit(tree)

        self._index[str(path)] = FileIndex(
            path         = str(path),
            content_hash = self._hashes.get(str(path), ""),
            symbols      = extractor.symbols,
            imports      = extractor.imports,
            calls        = extractor.calls,
        )
