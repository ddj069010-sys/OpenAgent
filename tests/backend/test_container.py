"""
Unit tests for OpenAgent IoC Container and Capability Registry (Target Architecture v3 - §18)
"""

import pytest
from backend.capability_registry import CapabilityRegistry
from backend.container import ServiceContainer

def test_capability_registry_initialization():
    registry = CapabilityRegistry()
    caps = registry.probe_all()
    assert isinstance(caps, dict)
    assert "docker" in caps
    assert "gpu.nvidia" in caps
    assert "local_model_manager" in caps
    assert "tmux" in caps
    assert "git" in caps

def test_capability_registry_queries():
    registry = CapabilityRegistry()
    assert isinstance(registry.has("git"), bool)
    metadata = registry.get("gpu.nvidia")
    assert isinstance(metadata, dict)
    assert "available" in metadata

def test_container_registration_and_resolution():
    container = ServiceContainer()
    assert container.has("CapabilityRegistry")
    
    cap_reg = container.resolve("CapabilityRegistry")
    assert isinstance(cap_reg, CapabilityRegistry)

def test_container_factory_registration():
    container = ServiceContainer()
    container.register_factory("dummy_service", lambda: {"status": "ok"})
    
    assert container.has("dummy_service")
    service = container.resolve("dummy_service")
    assert service == {"status": "ok"}

def test_container_unregistered_service_raises_keyerror():
    container = ServiceContainer()
    with pytest.raises(KeyError):
        container.resolve("NonExistentService")
