"""
OpenAgent Capability Registry (Target Architecture v3 - §18.4)

Provides a centralized, queryable source of truth for runtime environment capabilities
(Docker daemon, local GGUF models, GPU VRAM, browser engines, LSP servers).
"""

import os
import shutil
import subprocess
from typing import Dict, Any, Optional

class CapabilityRegistry:
    """Centralized, queryable registry for runtime platform capabilities."""

    def __init__(self):
        self._capabilities: Dict[str, Any] = {}
        self.probe_all()

    def probe_all(self) -> Dict[str, Any]:
        """Probes all runtime environment capabilities."""
        self._capabilities["docker"] = self._check_docker()
        self._capabilities["gpu.nvidia"] = self._check_nvidia_gpu()
        self._capabilities["local_model_manager"] = self._check_local_model_manager()
        self._capabilities["browser.playwright"] = self._check_browser()
        self._capabilities["tmux"] = shutil.which("tmux") is not None
        self._capabilities["git"] = shutil.which("git") is not None
        return self._capabilities

    def _check_docker(self) -> bool:
        """Checks if Docker daemon is available and responsive."""
        if not shutil.which("docker"):
            return False
        try:
            res = subprocess.run(["docker", "info"], capture_output=True, timeout=2)
            return res.returncode == 0
        except Exception:
            return False

    def _check_nvidia_gpu(self) -> Dict[str, Any]:
        """Probes NVIDIA GPU presence and available VRAM using nvidia-smi."""
        if not shutil.which("nvidia-smi"):
            return {"available": False, "vram_free_mb": 0, "vram_total_mb": 0}
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free,memory.total,name", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3
            )
            if res.returncode == 0 and res.stdout.strip():
                parts = [p.strip() for p in res.stdout.strip().split(",")]
                return {
                    "available": True,
                    "vram_free_mb": int(parts[0]),
                    "vram_total_mb": int(parts[1]),
                    "gpu_name": parts[2] if len(parts) > 2 else "NVIDIA GPU"
                }
        except Exception:
            pass
        return {"available": False, "vram_free_mb": 0, "vram_total_mb": 0}

    def _check_local_model_manager(self) -> Dict[str, Any]:
        """Checks local model manager directory and GGUF availability."""
        models_dir = os.path.expanduser("~/llama.cpp/models")
        exists = os.path.exists(models_dir)
        gguf_files = []
        if exists:
            gguf_files = [f for f in os.listdir(models_dir) if f.endswith(".gguf")]
        return {
            "dir_exists": exists,
            "models_dir": models_dir,
            "gguf_count": len(gguf_files),
            "models": gguf_files
        }

    def _check_browser(self) -> bool:
        """Checks if browser automation engine / playwright is installed."""
        try:
            import playwright
            return True
        except ImportError:
            return False

    def has(self, capability_key: str) -> bool:
        """Queries whether a specific capability key is available."""
        val = self._capabilities.get(capability_key)
        if isinstance(val, dict):
            return val.get("available", False) or val.get("dir_exists", False)
        return bool(val)

    def get(self, capability_key: str, default: Any = None) -> Any:
        """Retrieves raw metadata for a capability key."""
        return self._capabilities.get(capability_key, default)


# Default Singleton Capability Registry Instance
capability_registry = CapabilityRegistry()
