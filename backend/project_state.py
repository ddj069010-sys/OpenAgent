"""
OpenAgent Project State  (Phase 8.1 — Project Awareness Layer)

Single source of truth for the current session's context.
Loaded first on every session start; eliminates full-repo re-analysis.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class ProjectState:
    phase:            int
    architecture_ver: str
    current_goal:     str
    active_feature:   Optional[str]
    active_bug:       Optional[str]
    git_branch:       str
    latest_commit:    str
    hardware_profile: str
    active_model:     str
    vram_limit_mb:    float
    completed:        List[str]    = field(default_factory=list)
    deferred:         List[str]    = field(default_factory=list)
    blockers:         List[str]    = field(default_factory=list)
    next_step:        str          = ""
    status:           str          = "healthy"   # "healthy" | "degraded" | "blocked"
    updated_at:       float        = field(default_factory=time.time)


class ProjectStateStore:
    """
    Reads, updates, and persists project_state.json.
    First thing loaded on every session — zero redundant repo scanning.
    """

    def __init__(self, state_file: str | Path) -> None:
        self.path = Path(state_file)
        self._state: Optional[ProjectState] = None

    # ── I/O ──────────────────────────────────────────────────────────────────

    def load(self) -> ProjectState:
        """Reads state from disk, or creates a minimal default if absent."""
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._state = ProjectState(**data)
        else:
            self._state = self._default_state()
        return self._state

    def save(self) -> None:
        """Persists current in-memory state to disk atomically."""
        if self._state is None:
            return
        self._state.updated_at = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self._state), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ── Mutations ─────────────────────────────────────────────────────────────

    def update(self, **kwargs) -> ProjectState:
        """Applies keyword-argument updates to the current state and saves."""
        s = self._state or self.load()
        for k, v in kwargs.items():
            if hasattr(s, k):
                setattr(s, k, v)
        self.save()
        return s

    def mark_completed(self, item: str) -> None:
        s = self._state or self.load()
        if item not in s.completed:
            s.completed.append(item)
        self.save()

    def add_blocker(self, blocker: str) -> None:
        s = self._state or self.load()
        if blocker not in s.blockers:
            s.blockers.append(blocker)
        self.save()

    def clear_blocker(self, blocker: str) -> None:
        s = self._state or self.load()
        s.blockers = [b for b in s.blockers if b != blocker]
        self.save()

    @property
    def state(self) -> Optional[ProjectState]:
        return self._state

    # ── Defaults ──────────────────────────────────────────────────────────────

    @staticmethod
    def _default_state() -> ProjectState:
        return ProjectState(
            phase=8,
            architecture_ver="v3",
            current_goal="Autonomous Execution Framework",
            active_feature=None,
            active_bug=None,
            git_branch="main",
            latest_commit="HEAD",
            hardware_profile="rtx3050_laptop_4gb",
            active_model="qwen2.5-coder-7b-instruct",
            vram_limit_mb=3800.0,
            completed=[
                "IoC", "ConfigService", "TaskStateMachine", "CheckpointStore",
                "RepositoryIndex", "ContextEngine", "ReflectionEngine",
                "TaskPlanner", "WorkspaceKnowledgeBase", "UnifiedPropertyGraph",
                "EnvironmentManager", "ResourceScheduler", "JobScheduler",
                "WorkspaceManager", "RepositoryWatcher", "PersistentMemory",
                "EnvironmentBootstrap", "DeveloperExperience", "PerformanceProfiler",
                "CognitiveEngine", "AutonomousExecutionFramework",
            ],
            next_step="ProjectDirector",
            status="healthy",
        )
