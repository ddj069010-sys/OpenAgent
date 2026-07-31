"""
OpenAgent Repository Watcher  (Phase 6 — Phase E)

Incremental filesystem mtime scanner to update the RepositoryIndex,
WorkspaceKnowledgeBase, and UnifiedPropertyGraph only for modified/new/deleted files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Set

if TYPE_CHECKING:
    from backend.repository_index import RepositoryIndex
    from backend.knowledge_base import WorkspaceKnowledgeBase
    from backend.property_graph import UnifiedPropertyGraph


@dataclass
class FileChanges:
    added:    Set[str] = field(default_factory=set)
    modified: Set[str] = field(default_factory=set)
    deleted:  Set[str] = field(default_factory=set)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.deleted)


class RepositoryWatcher:
    """
    Tracks filesystem modification times to incrementally update indexing structures.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root)
        self._mtimes: Dict[str, float] = {}

    def scan_for_changes(self, extensions: tuple[str, ...] = (".py", ".md", ".txt")) -> FileChanges:
        """
        Scans workspace root for modified, new, or deleted files.
        Returns a FileChanges descriptor.
        """
        current_files: Dict[str, float] = {}

        # 1. Walk directory and collect mtimes
        for root_dir, _, filenames in os.walk(self.root):
            # Ignore hidden files, node_modules, build directories
            parts = Path(root_dir).relative_to(self.root).parts
            if any(p.startswith(".") or p in ("node_modules", "run", "snapshots") for p in parts):
                continue

            for fname in filenames:
                file_path = Path(root_dir) / fname
                if file_path.suffix not in extensions:
                    continue

                try:
                    current_files[str(file_path)] = file_path.stat().st_mtime
                except OSError:
                    continue

        # 2. Determine added and modified files
        added = set()
        modified = set()
        for fpath, mtime in current_files.items():
            if fpath not in self._mtimes:
                added.add(fpath)
            elif mtime > self._mtimes[fpath]:
                modified.add(fpath)

        # 3. Determine deleted files
        deleted = set(self._mtimes.keys()) - set(current_files.keys())

        # 4. Save state
        self._mtimes = current_files

        return FileChanges(added=added, modified=modified, deleted=deleted)

    def incremental_sync(
        self,
        changes: FileChanges,
        repo_index: "RepositoryIndex",
        kb: "WorkspaceKnowledgeBase",
        property_graph: "UnifiedPropertyGraph"
    ) -> None:
        """Propagates detected file changes incrementally to relevant subsystems."""
        if not changes.has_changes:
            return

        # 1. Update RepositoryIndex
        for path_str in changes.deleted:
            repo_index._index.pop(path_str, None)
            repo_index._hashes.pop(path_str, None)
        
        # Force re-indexing of added/modified files
        for path_str in (changes.added | changes.modified):
            # Clearing the cached hash forces RepositoryIndex._index_file to parse it again
            repo_index._hashes.pop(path_str, None)
            repo_index._index_file(Path(path_str))

        # 2. Update UnifiedPropertyGraph
        property_graph.build(repo_index)

        # 3. Update Knowledge Base (evict deleted, re-segment added/modified)
        kb._segments = [seg for seg in kb._segments if seg.filepath not in changes.deleted]
        # Re-segment new/modified files
        for path_str in (changes.added | changes.modified):
            # Remove old segments for this file
            kb._segments = [seg for seg in kb._segments if seg.filepath != path_str]
            try:
                content = Path(path_str).read_text(encoding="utf-8", errors="replace")
                kb._segment_file(path_str, content)
            except OSError:
                continue

        # Recalculate document frequencies for TF-IDF
        kb._num_docs = len(kb._segments)
        kb._calculate_doc_frequencies()
