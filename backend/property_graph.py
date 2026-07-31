"""
OpenAgent Unified Property Graph  (Target Architecture v3 §26 / Architecture v1 §10)

Stores relationships between workspace components:
  Function → Class → File → Package → Module → Owner → Git History → Tests

Design decisions:
- Node-edge graph is stored as an adjacency list dictionary — extremely lightweight,
  running with zero external database dependencies.
- Infers structural relationships (e.g. tests mapping) and code complexity
  (approximated using lines of code and function definition count).
- Integrates with RepositoryIndex to populate graph nodes from AST.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from backend.repository_index import RepositoryIndex


# ── Graph structures ──────────────────────────────────────────────────────────

@dataclass
class GraphNode:
    id:       str                        # Unique node identifier
    kind:     str                        # "file" | "class" | "function" | "method" | "package"
    props:    dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    source:   str
    target:   str
    kind:     str                        # "contains" | "calls" | "imports" | "tested_by"
    props:    dict[str, Any] = field(default_factory=dict)


# ── Unified Property Graph ───────────────────────────────────────────────────

class UnifiedPropertyGraph:
    """
    Queryable graph representation of the repository's code, structure, ownership,
    and testing.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self._root = Path(workspace_root)
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge]      = []

    # ── Graph Building ────────────────────────────────────────────────────────

    def build(self, repo_index: "RepositoryIndex") -> None:
        """
        Builds the graph using the AST information parsed by RepositoryIndex,
        coupled with file hierarchy and git-based metadata discovery.
        """
        self.nodes.clear()
        self.edges.clear()

        # Step 1: Add workspace files and directory hierarchy
        for path_str in list(repo_index._index.keys()):
            rel_path = os.path.relpath(path_str, start=str(self._root))
            file_node_id = f"file:{rel_path}"

            # Estimate file complexity (LOC)
            loc = 0
            try:
                with open(path_str, "r", encoding="utf-8", errors="replace") as f:
                    loc = len(f.readlines())
            except OSError:
                pass

            owner = self._git_owner(path_str)

            self.nodes[file_node_id] = GraphNode(
                id=file_node_id,
                kind="file",
                props={
                    "path": rel_path,
                    "loc": loc,
                    "owner": owner
                }
            )

            # Step 2: Extract symbols (Classes, Functions, Methods)
            for sym in repo_index.symbols_in(path_str):
                sym_node_id = f"{sym.kind}:{rel_path}:{sym.name}"

                self.nodes[sym_node_id] = GraphNode(
                    id=sym_node_id,
                    kind=sym.kind,
                    props={
                        "name": sym.name,
                        "line": sym.line,
                        "parent": sym.parent
                    }
                )

                # Link file → symbol
                if sym.kind == "class" or (sym.kind == "function" and not sym.parent):
                    self.add_edge(file_node_id, sym_node_id, "contains")
                elif sym.kind == "method" and sym.parent:
                    # Link class → method
                    class_id = f"class:{rel_path}:{sym.parent}"
                    if class_id in self.nodes:
                        self.add_edge(class_id, sym_node_id, "contains")

            # Step 3: Link imports (File → Imported File / Package)
            for imp in repo_index.imports_of(path_str):
                self.add_edge(file_node_id, f"package:{imp}", "imports")

        # Step 4: Map test files to source files
        self._map_test_relationships()

    def add_edge(self, source: str, target: str, kind: str, props: Optional[dict] = None) -> None:
        self.edges.append(GraphEdge(
            source=source,
            target=target,
            kind=kind,
            props=props or {}
        ))

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_dependents(self, node_id: str) -> list[str]:
        """Returns all nodes that import or depend on the given node."""
        return [e.source for e in self.edges if e.target == node_id]

    def get_contains(self, file_node_id: str) -> list[GraphNode]:
        """Returns all symbols defined inside a file node."""
        targets = [e.target for e in self.edges if e.source == file_node_id and e.kind == "contains"]
        return [self.nodes[tid] for tid in targets if tid in self.nodes]

    def get_test_files(self, source_file_id: str) -> list[str]:
        """Finds test files that verify code in the source file."""
        return [e.target for e in self.edges if e.source == source_file_id and e.kind == "tested_by"]

    def summary(self) -> dict:
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "kinds": list(set(n.kind for n in self.nodes.values())),
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _git_owner(self, filepath: str) -> str:
        """Runs git log to find the most frequent author of the file."""
        try:
            cmd = ["git", "log", "-n", "10", "--format=%ae", filepath]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
            authors = out.splitlines()
            if not authors:
                return "unknown"
            # Return most frequent author
            return max(set(authors), key=authors.count)
        except (subprocess.SubprocessError, FileNotFoundError):
            return "unknown"

    def _map_test_relationships(self) -> None:
        """Infers tested_by relationships by matching source files to test files."""
        files = [n for n in self.nodes.values() if n.kind == "file"]
        test_files = [f for f in files if "test" in f.props.get("path", "").lower()]
        source_files = [f for f in files if f not in test_files]

        for src in source_files:
            src_name = Path(src.props["path"]).stem
            for tf in test_files:
                tf_name = Path(tf.props["path"]).stem
                # Common naming convention matching: test_auth.py maps to auth.py
                if src_name in tf_name or tf_name in src_name:
                    self.add_edge(src.id, tf.id, "tested_by")
