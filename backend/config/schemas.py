"""
OpenAgent Configuration Schemas  (Target Architecture v3 §18.5)

Typed Pydantic models for every config file under config/.
All other modules import these types; they never read YAML directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


# ── model.yaml ──────────────────────────────────────────────────────────────

class ModelEntry(BaseModel):
    n_gpu_layers: int = -1
    context_size: int = 4096
    cost_per_token: float = 0.0
    exclude_from_agent_profiles: bool = False


class LocalModelManagerConfig(BaseModel):
    port: int = 19000
    models_dir: str = "~/llama.cpp/models"
    llama_server_bin: str = "~/llama.cpp/llama-server"
    llama_server_port: int = 8080
    health_check_timeout_ms: int = 800
    max_swap_retries: int = 3
    provisioning_delay_ms: int = 2000


class ModelConfig(BaseModel):
    local_model_manager: LocalModelManagerConfig = Field(
        default_factory=LocalModelManagerConfig
    )
    models: dict[str, ModelEntry] = Field(default_factory=dict)


# ── hardware_profile.yaml ────────────────────────────────────────────────────

class GpuProfile(BaseModel):
    name: str = "Unknown GPU"
    vram_total_mb: int = 4096
    vram_safety_margin_mb: int = 256
    vram_minimum_free_mb: int = 400


class SystemProfile(BaseModel):
    ram_total_mb: int = 24576
    ram_budget_mb: int = 6144


class InferenceProfile(BaseModel):
    single_model_policy: bool = True
    default_n_gpu_layers: int = -1
    default_context_size: int = 4096


class HardwareProfile(BaseModel):
    gpu: GpuProfile = Field(default_factory=GpuProfile)
    system: SystemProfile = Field(default_factory=SystemProfile)
    inference: InferenceProfile = Field(default_factory=InferenceProfile)


# ── security_policy.yaml ────────────────────────────────────────────────────

class ToolPermissionTiers(BaseModel):
    read_only: list[str] = Field(default_factory=list)
    write_isolated: list[str] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list)
    destructive: list[str] = Field(default_factory=list)


class SandboxConfig(BaseModel):
    workspace_isolation: bool = True
    projects_dir: str = "~/.openhands/agent-canvas/workspaces"
    protected_paths: list[str] = Field(default_factory=list)


class CredentialConfig(BaseModel):
    mask_in_logs: bool = True
    allowed_env_vars: list[str] = Field(default_factory=list)


class SecurityPolicy(BaseModel):
    tool_permission_tiers: ToolPermissionTiers = Field(
        default_factory=ToolPermissionTiers
    )
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    credentials: CredentialConfig = Field(default_factory=CredentialConfig)


# ── model_routing.yaml ───────────────────────────────────────────────────────

class LocalModelCostOverride(BaseModel):
    model: str
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0


class LiteLLMConfig(BaseModel):
    drop_params: bool = True
    modify_params: bool = True
    native_tool_calling: bool = True
    stream: bool = False
    timeout_seconds: int = 300
    num_retries: int = 3
    max_message_chars: int = 30000
    local_model_cost_overrides: list[LocalModelCostOverride] = Field(
        default_factory=list
    )


class ModelRoutingConfig(BaseModel):
    local_model_manager: LocalModelManagerConfig = Field(
        default_factory=LocalModelManagerConfig
    )
    litellm: LiteLLMConfig = Field(default_factory=LiteLLMConfig)
