"""
OpenAgent Environment Manager  (Phase 6 — Phase A)

Manages isolated runtime directories, log rotation, stale cache eviction,
checkpoint database compression, and orphaned session cleanup.

Directory layout:
  <workspace_root>/run/
    ├── logs/
    ├── cache/
    ├── workspace/
    ├── indexes/
    ├── embeddings/
    ├── artifacts/
    ├── memory/
    ├── sessions/
    ├── temp/
    └── models/
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DiskTelemetry:
    total_bytes:     int
    used_bytes:      int
    free_bytes:      int
    percent_used:    float


class EnvironmentManager:
    """
    Handles runtime environment directory bootstrapping, disk usage telemetry,
    log rotation, and garbage collection of cache/sessions.
    """

    def __init__(self, run_root: str | Path) -> None:
        self.root = Path(run_root)
        self.subdirs = [
            "logs", "cache", "workspace", "indexes", "embeddings",
            "artifacts", "memory", "sessions", "temp", "models"
        ]

    # ── Initialization ────────────────────────────────────────────────────────

    def bootstrap(self) -> dict[str, str]:
        """Creates the runtime folder structure if missing."""
        paths = {}
        for folder in self.subdirs:
            p = self.root / folder
            p.mkdir(parents=True, exist_ok=True)
            paths[folder] = str(p)
        return paths

    # ── Cleanup Operations ────────────────────────────────────────────────────

    def rotate_logs(self, max_size_bytes: int = 10 * 1024 * 1024) -> int:
        """
        Rotates files in logs/ that exceed max_size_bytes.
        Returns the number of log files rotated.
        """
        logs_dir = self.root / "logs"
        if not logs_dir.exists():
            return 0

        rotated = 0
        for log_file in logs_dir.glob("*.log"):
            if log_file.is_file() and log_file.stat().st_size > max_size_bytes:
                backup = log_file.with_suffix(f".log.{int(time.time())}")
                try:
                    shutil.move(str(log_file), str(backup))
                    rotated += 1
                except OSError:
                    continue
        return rotated

    def evict_stale_cache(self, max_age_seconds: int = 86400) -> int:
        """
        Removes cache files older than max_age_seconds.
        Returns the number of files deleted.
        """
        cache_dir = self.root / "cache"
        if not cache_dir.exists():
            return 0

        evicted = 0
        now = time.time()
        for item in cache_dir.rglob("*"):
            if item.is_file():
                age = now - item.stat().st_mtime
                if age > max_age_seconds:
                    try:
                        item.unlink()
                        evicted += 1
                    except OSError:
                        continue
        return evicted

    def remove_orphan_sessions(self, max_idle_seconds: int = 3600 * 12) -> int:
        """
        Deletes session workspace and metadata folders that have been inactive.
        Returns the number of sessions cleaned.
        """
        sessions_dir = self.root / "sessions"
        if not sessions_dir.exists():
            return 0

        removed = 0
        now = time.time()
        for session_folder in sessions_dir.iterdir():
            if session_folder.is_dir():
                # Check modification time of folder or metadata file inside
                mtime = session_folder.stat().st_mtime
                meta_file = session_folder / "session_meta.json"
                if meta_file.exists():
                    mtime = max(mtime, meta_file.stat().st_mtime)

                if now - mtime > max_idle_seconds:
                    try:
                        shutil.rmtree(session_folder)
                        removed += 1
                    except OSError:
                        continue
        return removed

    def clean_temp(self) -> int:
        """Cleans all files in run/temp/."""
        temp_dir = self.root / "temp"
        if not temp_dir.exists():
            return 0

        cleaned = 0
        for item in temp_dir.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
                cleaned += 1
            except OSError:
                continue
        return cleaned

    # ── Telemetry ─────────────────────────────────────────────────────────────

    def monitor_disk(self) -> DiskTelemetry:
        """Returns storage utilization metrics for the run root directory."""
        # Use shutil.disk_usage
        total, used, free = shutil.disk_usage(self.root)
        percent = (used / total) * 100 if total > 0 else 0.0
        return DiskTelemetry(
            total_bytes=total,
            used_bytes=used,
            free_bytes=free,
            percent_used=round(percent, 2)
        )
