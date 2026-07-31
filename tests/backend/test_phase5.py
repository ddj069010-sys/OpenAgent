"""
Unit tests for Phase 5 subsystems:
  WorkspaceKnowledgeBase, UnifiedPropertyGraph
(Target Architecture v3 §25, §26)
"""

import pytest
import tempfile
from pathlib import Path

from backend.knowledge_base import WorkspaceKnowledgeBase, DocumentSegment
from backend.property_graph import UnifiedPropertyGraph, GraphNode, GraphEdge
from backend.repository_index import RepositoryIndex


# ═══════════════════════════════════════════════════════════════════════════
# WorkspaceKnowledgeBase tests
# ═══════════════════════════════════════════════════════════════════════════

class TestWorkspaceKnowledgeBase:

    def _setup_docs(self, tmp_path: Path) -> WorkspaceKnowledgeBase:
        # Create some documentation files
        (tmp_path / "README.md").write_text(
            "# OpenAgent Architecture\n"
            "This document outlines the v3 Enterprise architecture.\n"
            "## IoC Container\n"
            "The container handles service registration and resolution.\n"
            "## Telemetry\n"
            "GPU VRAM telemetry is exposed via a local-model-manager port.\n"
        )
        (tmp_path / "design_decisions.txt").write_text(
            "We decided to use SQLite WAL mode for task checkpoint storage.\n"
            "This ensures crash recovery works with minimal overhead.\n"
        )
        kb = WorkspaceKnowledgeBase(tmp_path)
        kb.index_documentation()
        return kb

    def test_index_documentation_counts_files(self, tmp_path):
        kb = self._setup_docs(tmp_path)
        assert len(kb._segments) >= 3  # README sections + design decisions

    def test_search_by_exact_match(self, tmp_path):
        kb = self._setup_docs(tmp_path)
        res = kb.search("IoC Container")
        assert res.total_found > 0
        assert "container" in res.results[0].content.lower()

    def test_search_tf_idf_ranking(self, tmp_path):
        kb = self._setup_docs(tmp_path)
        # Search term "SQLite" should rank design decisions higher than README
        res = kb.search("SQLite WAL checkpoint")
        assert res.total_found > 0
        assert "design_decisions.txt" in res.results[0].filepath

    def test_search_limit_respected(self, tmp_path):
        kb = self._setup_docs(tmp_path)
        res = kb.search("architecture", limit=1)
        assert len(res.results) == 1

    def test_empty_query_returns_no_results(self, tmp_path):
        kb = self._setup_docs(tmp_path)
        res = kb.search("")
        assert len(res.results) == 0


# ═══════════════════════════════════════════════════════════════════════════
# UnifiedPropertyGraph tests
# ═══════════════════════════════════════════════════════════════════════════

class TestUnifiedPropertyGraph:

    def _setup_graph(self, tmp_path: Path) -> tuple[UnifiedPropertyGraph, RepositoryIndex]:
        # Create a Python file structure
        (tmp_path / "auth.py").write_text(
            "class Authenticator:\n"
            "    def login(self): pass\n"
            "def check_session(): pass\n"
        )
        (tmp_path / "test_auth.py").write_text(
            "import auth\n"
            "def test_authenticator_login(): pass\n"
        )
        idx = RepositoryIndex(tmp_path)
        idx.index_workspace()

        graph = UnifiedPropertyGraph(tmp_path)
        graph.build(idx)
        return graph, idx

    def test_graph_node_and_edge_counts(self, tmp_path):
        graph, _ = self._setup_graph(tmp_path)
        summary = graph.summary()
        assert summary["node_count"] > 0
        assert summary["edge_count"] > 0

    def test_graph_contains_correct_kinds(self, tmp_path):
        graph, _ = self._setup_graph(tmp_path)
        kinds = graph.summary()["kinds"]
        assert "file" in kinds
        assert "class" in kinds
        assert "method" in kinds

    def test_query_contains_edges(self, tmp_path):
        graph, _ = self._setup_graph(tmp_path)
        # Find file node for auth.py
        file_node_id = "file:auth.py"
        assert file_node_id in graph.nodes

        symbols = graph.get_contains(file_node_id)
        names = [s.props["name"] for s in symbols]
        assert "Authenticator" in names
        assert "check_session" in names

    def test_query_test_relationship_mapping(self, tmp_path):
        graph, _ = self._setup_graph(tmp_path)
        src_file_id = "file:auth.py"
        test_files = graph.get_test_files(src_file_id)
        assert len(test_files) == 1
        assert "test_auth.py" in test_files[0]
