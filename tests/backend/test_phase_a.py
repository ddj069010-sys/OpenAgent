"""
Unit tests for Phase A: EnvironmentManager
"""

import time
import pytest
from pathlib import Path
from backend.environment_manager import EnvironmentManager, DiskTelemetry


class TestEnvironmentManager:

    def test_bootstrap_creates_all_subdirs(self, tmp_path):
        mgr = EnvironmentManager(tmp_path)
        paths = mgr.bootstrap()
        
        assert len(paths) == 10
        for name in mgr.subdirs:
            assert name in paths
            assert (tmp_path / name).exists()
            assert (tmp_path / name).is_dir()

    def test_log_rotation_skips_small_files(self, tmp_path):
        mgr = EnvironmentManager(tmp_path)
        mgr.bootstrap()
        
        log_file = tmp_path / "logs" / "test.log"
        log_file.write_text("small log")
        
        rotated = mgr.rotate_logs(max_size_bytes=1000)
        assert rotated == 0
        assert log_file.exists()

    def test_log_rotation_rotates_large_files(self, tmp_path):
        mgr = EnvironmentManager(tmp_path)
        mgr.bootstrap()
        
        log_file = tmp_path / "logs" / "large.log"
        log_file.write_text("A" * 2000)
        
        rotated = mgr.rotate_logs(max_size_bytes=1000)
        assert rotated == 1
        assert not log_file.exists()
        
        # Check that backup file exists
        backups = list((tmp_path / "logs").glob("large.log.*"))
        assert len(backups) == 1

    def test_cache_eviction_only_deletes_stale_files(self, tmp_path):
        mgr = EnvironmentManager(tmp_path)
        mgr.bootstrap()
        
        fresh = tmp_path / "cache" / "fresh.cache"
        fresh.write_text("fresh content")
        
        stale = tmp_path / "cache" / "stale.cache"
        stale.write_text("stale content")
        
        # Set modification time of stale to 2 hours ago
        two_hours_ago = time.time() - 7200
        stale.touch()
        import os
        os.utime(stale, (two_hours_ago, two_hours_ago))
        
        # Evict cache older than 1 hour (3600 seconds)
        evicted = mgr.evict_stale_cache(max_age_seconds=3600)
        assert evicted == 1
        assert fresh.exists()
        assert not stale.exists()

    def test_remove_orphan_sessions(self, tmp_path):
        mgr = EnvironmentManager(tmp_path)
        mgr.bootstrap()
        
        session_dir = tmp_path / "sessions" / "session_1"
        session_dir.mkdir()
        meta = session_dir / "session_meta.json"
        meta.write_text("{}")
        
        # Set modification times to 2 hours ago
        two_hours_ago = time.time() - 7200
        import os
        os.utime(session_dir, (two_hours_ago, two_hours_ago))
        os.utime(meta, (two_hours_ago, two_hours_ago))
        
        # Clean sessions older than 1 hour (3600 seconds)
        removed = mgr.remove_orphan_sessions(max_idle_seconds=3600)
        assert removed == 1
        assert not session_dir.exists()

    def test_monitor_disk_usage(self, tmp_path):
        mgr = EnvironmentManager(tmp_path)
        telemetry = mgr.monitor_disk()
        
        assert isinstance(telemetry, DiskTelemetry)
        assert telemetry.total_bytes > 0
        assert telemetry.free_bytes >= 0
        assert 0.0 <= telemetry.percent_used <= 100.0
