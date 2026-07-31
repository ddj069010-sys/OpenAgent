"""
OpenAgent Workspace Manager  (Phase 6 — Phase D)

Manages isolated workspace paths, metadata lifecycle, temp project generation,
snapshots/checkpoints of workspace code, and automatic sandbox directory cleanup.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class WorkspaceMetadata:
    id:          str
    name:        str
    root_path:   str
    created_at:  float
    is_temp:     bool = False
    history:     List[str] = field(default_factory=list)


class WorkspaceManager:
    """
    Manages isolated projects, temporary workspaces, and snapshots for autonomous coding.
    """

    def __init__(self, manager_root: str | Path) -> None:
        self.root = Path(manager_root)
        self.workspaces_dir = self.root / "workspaces"
        self.snapshots_dir = self.root / "snapshots"
        self._workspaces: Dict[str, WorkspaceMetadata] = {}
        
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self._load_metadata()

    # ── Workspace Lifecycle ───────────────────────────────────────────────────

    def create_workspace(self, name: str, is_temp: bool = False) -> WorkspaceMetadata:
        """Creates a new isolated workspace directory and saves metadata."""
        # Use uuid-based unique suffix to avoid collisions in fast test environments
        import uuid
        workspace_id = f"ws_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        ws_path = self.workspaces_dir / workspace_id
        ws_path.mkdir(parents=True, exist_ok=True)

        meta = WorkspaceMetadata(
            id=workspace_id,
            name=name,
            root_path=str(ws_path),
            created_at=time.time(),
            is_temp=is_temp
        )
        self._workspaces[workspace_id] = meta
        self._save_metadata()
        return meta

    def get_workspace(self, workspace_id: str) -> Optional[WorkspaceMetadata]:
        return self._workspaces.get(workspace_id)

    def delete_workspace(self, workspace_id: str) -> bool:
        """Deletes a workspace and its files from disk."""
        meta = self.get_workspace(workspace_id)
        if not meta:
            return False

        ws_path = Path(meta.root_path)
        if ws_path.exists():
            shutil.rmtree(ws_path)

        self._workspaces.pop(workspace_id)
        self._save_metadata()
        return True

    def list_workspaces(self) -> List[WorkspaceMetadata]:
        return list(self._workspaces.values())

    # ── Snapshot Operations ───────────────────────────────────────────────────

    def take_snapshot(self, workspace_id: str, description: str = "") -> Optional[str]:
        """
        Creates a zip/tar snapshot of the workspace files to rollback in case
        of major agent failures. Returns snapshot ID/path.
        """
        meta = self.get_workspace(workspace_id)
        if not meta:
            return None

        ws_path = Path(meta.root_path)
        if not ws_path.exists():
            return None

        snap_id = f"snap_{workspace_id}_{int(time.time())}"
        snap_path = self.snapshots_dir / snap_id
        
        # Safe copy/zip operation
        try:
            shutil.make_archive(str(snap_path), "zip", str(ws_path))
            meta.history.append(f"Snapshot taken: {description} (ID: {snap_id})")
            self._save_metadata()
            return f"{snap_path}.zip"
        except OSError:
            return None

    # ── Metadata Persistence ──────────────────────────────────────────────────

    def _load_metadata(self) -> None:
        meta_file = self.root / "workspace_meta.json"
        if meta_file.exists():
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                for wid, wdata in data.items():
                    self._workspaces[wid] = WorkspaceMetadata(**wdata)
            except Exception:
                pass

    def _save_metadata(self) -> None:
        meta_file = self.root / "workspace_meta.json"
        data = {wid: asdict(wmeta) for wid, wmeta in self._workspaces.items()}
        meta_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
