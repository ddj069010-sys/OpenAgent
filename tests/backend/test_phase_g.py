"""
Unit tests for Phase G: EnvironmentBootstrap
"""

import pytest
from pathlib import Path
from backend.environment_bootstrap import EnvironmentBootstrap, BootstrapResult


class TestEnvironmentBootstrap:

    def test_run_checks_on_valid_workspace(self, tmp_path):
        bootstrap = EnvironmentBootstrap(tmp_path)
        res = bootstrap.run_checks()
        
        assert isinstance(res, BootstrapResult)
        assert res.python_version is not None
        assert res.workspace_ok is True
        
        # Verify healthy status matches critical errors count
        if len(res.critical_errors) == 0:
            assert res.is_healthy is True
        else:
            assert res.is_healthy is False

    def test_workspace_not_found_raises_critical_error(self):
        nonexistent = Path("/nonexistent/path/xyz_123")
        bootstrap = EnvironmentBootstrap(nonexistent)
        res = bootstrap.run_checks()
        
        assert res.workspace_ok is False
        assert len(res.critical_errors) > 0
        assert "Workspace path" in res.critical_errors[0]
        assert res.is_healthy is False
