"""
Unit tests for Phase D: WorkspaceManager
"""

import pytest
from pathlib import Path
from backend.workspace_manager import WorkspaceManager, WorkspaceMetadata


class TestWorkspaceManager:

    def test_workspace_creation(self, tmp_path):
        mgr = WorkspaceManager(tmp_path)
        
        ws = mgr.create_workspace("Test Project", is_temp=True)
        assert ws.name == "Test Project"
        assert ws.is_temp is True
        assert Path(ws.root_path).exists()
        assert Path(ws.root_path).is_dir()
        
        # Verify persistence/loading
        mgr2 = WorkspaceManager(tmp_path)
        ws_loaded = mgr2.get_workspace(ws.id)
        assert ws_loaded is not None
        assert ws_loaded.name == "Test Project"

    def test_list_and_delete_workspace(self, tmp_path):
        mgr = WorkspaceManager(tmp_path)
        
        ws1 = mgr.create_workspace("WS1")
        ws2 = mgr.create_workspace("WS2")
        
        all_ws = mgr.list_workspaces()
        assert len(all_ws) == 2
        
        mgr.delete_workspace(ws1.id)
        assert mgr.get_workspace(ws1.id) is None
        assert len(mgr.list_workspaces()) == 1

    def test_take_snapshot(self, tmp_path):
        mgr = WorkspaceManager(tmp_path)
        ws = mgr.create_workspace("WS1")
        
        # Write dummy file to workspace root
        ws_root = Path(ws.root_path)
        (ws_root / "code.py").write_text("print(123)")
        
        snap_zip = mgr.take_snapshot(ws.id, "initial commit")
        assert snap_zip is not None
        assert Path(snap_zip).exists()
        
        # Verify history update
        ws_updated = mgr.get_workspace(ws.id)
        assert len(ws_updated.history) == 1
        assert "initial commit" in ws_updated.history[0]
