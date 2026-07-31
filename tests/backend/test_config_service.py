"""
Unit tests for ConfigService and Pydantic schemas (Target Architecture v3 §18.5)
"""

import pytest
from backend.config.schemas import (
    ModelConfig, HardwareProfile, SecurityPolicy, ModelRoutingConfig,
    ModelEntry, GpuProfile,
)
from backend.config.service import ConfigService, get_config_service
from backend.container import ServiceContainer


# ── Schema defaults ──────────────────────────────────────────────────────────

def test_model_config_defaults():
    cfg = ModelConfig()
    assert cfg.local_model_manager.port == 19000
    assert isinstance(cfg.models, dict)

def test_hardware_profile_defaults():
    hw = HardwareProfile()
    assert hw.gpu.vram_total_mb == 4096
    assert hw.inference.single_model_policy is True

def test_security_policy_defaults():
    sec = SecurityPolicy()
    assert sec.sandbox.workspace_isolation is True
    assert sec.credentials.mask_in_logs is True

def test_model_routing_defaults():
    routing = ModelRoutingConfig()
    assert routing.litellm.drop_params is True
    assert routing.litellm.timeout_seconds == 300


# ── ConfigService loads real YAML files ──────────────────────────────────────

def test_config_service_loads_model_yaml():
    svc = ConfigService()
    assert svc.get_model_config().local_model_manager.port == 19000

def test_config_service_loads_hardware_profile():
    svc = ConfigService()
    hw = svc.get_hardware_profile()
    assert hw.gpu.vram_total_mb > 0

def test_config_service_vram_budget():
    svc = ConfigService()
    budget = svc.get_vram_budget_mb()
    total  = svc.get_vram_total_mb()
    assert budget < total, "Budget must be less than total (safety margin)"

def test_config_service_single_model_policy():
    svc = ConfigService()
    assert svc.is_single_model_policy() is True


# ── IoC container resolves ConfigService ─────────────────────────────────────

def test_container_resolves_config_service():
    container = ServiceContainer()
    assert container.has("ConfigService")
    svc = container.resolve("ConfigService")
    assert isinstance(svc, ConfigService)

def test_config_service_singleton_via_lru_cache():
    svc1 = get_config_service()
    svc2 = get_config_service()
    assert svc1 is svc2, "ConfigService must be a singleton"
