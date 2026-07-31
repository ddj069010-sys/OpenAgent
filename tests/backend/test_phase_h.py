"""
Unit tests for Phase H: DeveloperExperience
"""

import pytest
from pathlib import Path
from backend.developer_experience import DeveloperExperience
from backend.environment_bootstrap import BootstrapResult
from backend.resource_scheduler import ResourceTelemetry
from backend.capability_registry import CapabilityRegistry


class TestDeveloperExperience:

    def test_generate_dashboard_contents(self, tmp_path):
        dx = DeveloperExperience(tmp_path)
        
        # Mock inputs
        bootstrap = BootstrapResult(
            python_version="3.12.3",
            node_version="20.10.0",
            gpu_available=True,
            tmux_available=True,
            ports_available={18000: True, 19000: False},
            workspace_ok=True,
            critical_errors=[]
        )
        
        telemetry = ResourceTelemetry(
            vram_used_mb=1200.0,
            ram_used_mb=8000.0,
            cpu_percent=45.5,
            context_size=8192
        )
        
        registry = CapabilityRegistry()
        # Mock git capability manually since host might not have Docker / exact configs
        registry._capabilities["git"] = True
        registry._capabilities["docker"] = False
        
        perf = {
            "indexing": 0.150,
            "planning": 0.050
        }
        
        content = dx.generate_dashboard(bootstrap, telemetry, registry, perf)
        
        # Verify content keywords are in markdown
        assert "# OpenAgent Diagnostics & Developer Dashboard" in content
        assert "🟢 HEALTHY" in content
        assert "3.12.3" in content
        assert "1200.0 MB" in content
        assert "git" in content
        assert "0.150s" in content
        
        # Verify dashboard file exists on disk
        assert dx.dashboard_path.exists()
        assert dx.dashboard_path.is_file()
