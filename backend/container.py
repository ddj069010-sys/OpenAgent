"""
OpenAgent IoC Composition Root (Target Architecture v3 - §18.2)

Provides Dependency Injection and Service Resolution for all backend modules.
Ensures subsystems consume abstractions/interfaces, never concrete dependencies directly.
"""

from typing import Dict, Any, Callable
from backend.capability_registry import CapabilityRegistry, capability_registry
from backend.config.service import get_config_service
from backend.checkpoint_store import CheckpointStore, get_checkpoint_store
from backend.repository_index import RepositoryIndex
from backend.context_engine import ContextEngine
from backend.reflection_engine import ReflectionEngine
from backend.task_planner import TaskPlanner
from backend.knowledge_base import WorkspaceKnowledgeBase
from backend.property_graph import UnifiedPropertyGraph
from backend.environment_manager import EnvironmentManager
from backend.resource_scheduler import ResourceScheduler
from backend.job_scheduler import JobScheduler
from backend.workspace_manager import WorkspaceManager
from backend.repository_watcher import RepositoryWatcher
from backend.persistent_memory import PersistentMemory
from backend.environment_bootstrap import EnvironmentBootstrap
from backend.developer_experience import DeveloperExperience
from backend.performance_profiler import PerformanceProfiler


class ServiceContainer:
    """Lightweight Inversion of Control (IoC) Container."""

    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._factories: Dict[str, Callable[[], Any]] = {}
        self._setup_defaults()

    def _setup_defaults(self):
        """Registers default core services into composition root."""
        self.register_instance("CapabilityRegistry", capability_registry)
        # ConfigService: lazy singleton via factory so YAML is parsed once on first use
        self.register_factory("ConfigService", get_config_service)
        # CheckpointStore: lazy singleton for crash recovery persistence
        self.register_factory("CheckpointStore", get_checkpoint_store)
        # ReflectionEngine: transient — each task gets its own retry budget
        self.register_factory("ReflectionEngine", lambda: ReflectionEngine(max_attempts=3))
        # TaskPlanner: transient — each goal gets a fresh plan DAG
        self.register_factory("TaskPlanner", TaskPlanner)
        # RepositoryIndex: transient — bound to a specific workspace root
        self.register_factory("RepositoryIndex", lambda: RepositoryIndex)
        # ContextEngine: transient — returning class reference
        self.register_factory("ContextEngine", lambda: ContextEngine)
        # WorkspaceKnowledgeBase: transient — bound to a specific workspace root
        self.register_factory("WorkspaceKnowledgeBase", lambda: WorkspaceKnowledgeBase)
        # UnifiedPropertyGraph: transient — bound to a specific workspace root
        self.register_factory("UnifiedPropertyGraph", lambda: UnifiedPropertyGraph)
        # EnvironmentManager: transient — bound to a specific run path
        self.register_factory("EnvironmentManager", lambda: EnvironmentManager)
        # ResourceScheduler: singleton
        self.register_instance("ResourceScheduler", ResourceScheduler())
        # JobScheduler: singleton
        self.register_instance("JobScheduler", JobScheduler())
        # WorkspaceManager: transient — bound to a manager root
        self.register_factory("WorkspaceManager", lambda: WorkspaceManager)
        # RepositoryWatcher: transient — bound to a workspace root
        self.register_factory("RepositoryWatcher", lambda: RepositoryWatcher)
        # PersistentMemory: transient — bound to a file path
        self.register_factory("PersistentMemory", lambda: PersistentMemory)
        # EnvironmentBootstrap: transient — bound to a workspace path
        self.register_factory("EnvironmentBootstrap", lambda: EnvironmentBootstrap)
        # DeveloperExperience: transient — bound to a workspace root
        self.register_factory("DeveloperExperience", lambda: DeveloperExperience)
        # PerformanceProfiler: singleton
        self.register_instance("PerformanceProfiler", PerformanceProfiler())
        # CognitiveEngine: transient — bound to a workspace root
        self.register_factory("CognitiveEngine", lambda: __import__("backend.cognitive_engine", fromlist=["CognitiveEngine"]).CognitiveEngine)

    def register_instance(self, service_name: str, instance: Any) -> None:
        """Registers a singleton instance by service name."""
        self._services[service_name] = instance

    def register_factory(self, service_name: str, factory: Callable[[], Any]) -> None:
        """Registers a transient factory constructor by service name."""
        self._factories[service_name] = factory

    def resolve(self, service_name: str) -> Any:
        """Resolves a service by name. Throws KeyError if unregistered."""
        if service_name in self._services:
            return self._services[service_name]
        if service_name in self._factories:
            return self._factories[service_name]()
        raise KeyError(f"Service '{service_name}' not registered in OpenAgent IoC Container.")

    def has(self, service_name: str) -> bool:
        """Checks if a service is registered."""
        return service_name in self._services or service_name in self._factories


# Global Composition Root Singleton Instance
container = ServiceContainer()
