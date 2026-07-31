"""
Unit tests for Phase E: RepositoryWatcher
"""

import time
import pytest
from pathlib import Path
from backend.repository_watcher import RepositoryWatcher, FileChanges
from backend.repository_index import RepositoryIndex
from backend.knowledge_base import WorkspaceKnowledgeBase
from backend.property_graph import UnifiedPropertyGraph


class TestRepositoryWatcher:

    def test_initial_scan_adds_all_files(self, tmp_path):
        watcher = RepositoryWatcher(tmp_path)
        
        # Create some files
        (tmp_path / "a.py").write_text("def a(): pass")
        (tmp_path / "b.md").write_text("# Hello")
        
        changes = watcher.scan_for_changes()
        assert len(changes.added) == 2
        assert len(changes.modified) == 0
        assert len(changes.deleted) == 0

    def test_subsequent_scan_no_changes(self, tmp_path):
        watcher = RepositoryWatcher(tmp_path)
        (tmp_path / "a.py").write_text("def a(): pass")
        
        watcher.scan_for_changes()
        changes = watcher.scan_for_changes()
        assert not changes.has_changes

    def test_detect_modified_deleted_added(self, tmp_path):
        watcher = RepositoryWatcher(tmp_path)
        
        a_file = tmp_path / "a.py"
        b_file = tmp_path / "b.py"
        
        a_file.write_text("a = 1")
        b_file.write_text("b = 2")
        
        watcher.scan_for_changes()
        
        # 1. Modify a_file
        a_file.write_text("a = 10")
        # Touch utility to simulate delay
        import os
        now = time.time() + 2
        os.utime(a_file, (now, now))
        
        # 2. Delete b_file
        b_file.unlink()
        
        # 3. Add c_file
        c_file = tmp_path / "c.py"
        c_file.write_text("c = 3")
        os.utime(c_file, (now, now))
        
        changes = watcher.scan_for_changes()
        assert str(a_file) in changes.modified
        assert str(b_file) in changes.deleted
        assert str(c_file) in changes.added

    def test_incremental_sync_propagates_correctly(self, tmp_path):
        # Setup indexes
        repo_index = RepositoryIndex(tmp_path)
        kb = WorkspaceKnowledgeBase(tmp_path)
        pg = UnifiedPropertyGraph(tmp_path)
        
        watcher = RepositoryWatcher(tmp_path)
        
        # Initial scan
        a_file = tmp_path / "a.py"
        a_file.write_text("class MyClass:\n    pass\n")
        
        changes = watcher.scan_for_changes()
        watcher.incremental_sync(changes, repo_index, kb, pg)
        
        assert "class:a.py:MyClass" in pg.nodes
        
        # Delete file and sync
        a_file.unlink()
        changes2 = watcher.scan_for_changes()
        watcher.incremental_sync(changes2, repo_index, kb, pg)
        
        # Verify MyClass is gone from pg
        assert "class:a.py:MyClass" not in pg.nodes
        assert not repo_index.symbols_in(str(a_file))
