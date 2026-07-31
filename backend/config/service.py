"""
OpenAgent Configuration Service  (Target Architecture v3 §18.5)

Single entry point for loading and accessing all typed configuration.
Every backend module MUST use ConfigService.get_*() — never read YAML directly.

Precedence (lowest → highest):
  config/*.yaml  →  environment variables  →  runtime API overrides
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

from backend.config.schemas import (
    HardwareProfile,
    ModelConfig,
    ModelRoutingConfig,
    SecurityPolicy,
)

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


def _load_yaml(filename: str) -> dict:
    """Load a YAML file from the config directory. Returns empty dict on error."""
    path = _CONFIG_DIR / filename
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class ConfigService:
    """
    Loads and caches every config file once at startup.
    Exposes typed accessors — no caller ever reads raw YAML.
    """

    def __init__(self) -> None:
        self._model_config      = ModelConfig(**_load_yaml("model.yaml"))
        self._hardware_profile  = HardwareProfile(**_load_yaml("hardware_profile.yaml"))
        self._security_policy   = SecurityPolicy(**_load_yaml("security_policy.yaml"))
        self._model_routing     = ModelRoutingConfig(**_load_yaml("model_routing.yaml"))

    # ── Typed accessors ──────────────────────────────────────────────────────

    def get_model_config(self) -> ModelConfig:
        """Returns validated model configuration (model.yaml)."""
        return self._model_config

    def get_hardware_profile(self) -> HardwareProfile:
        """Returns validated hardware profile (hardware_profile.yaml)."""
        return self._hardware_profile

    def get_security_policy(self) -> SecurityPolicy:
        """Returns validated security policy (security_policy.yaml)."""
        return self._security_policy

    def get_model_routing(self) -> ModelRoutingConfig:
        """Returns validated model routing configuration (model_routing.yaml)."""
        return self._model_routing

    # ── Convenience helpers ──────────────────────────────────────────────────

    def get_vram_total_mb(self) -> int:
        return self._hardware_profile.gpu.vram_total_mb

    def get_vram_budget_mb(self) -> int:
        """Returns the maximum VRAM the model manager may use."""
        return (
            self._hardware_profile.gpu.vram_total_mb
            - self._hardware_profile.gpu.vram_safety_margin_mb
        )

    def get_local_model_manager_port(self) -> int:
        return self._model_config.local_model_manager.port

    def is_single_model_policy(self) -> bool:
        return self._hardware_profile.inference.single_model_policy


@lru_cache(maxsize=1)
def get_config_service() -> ConfigService:
    """Returns the singleton ConfigService instance."""
    return ConfigService()
