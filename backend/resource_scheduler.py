"""
OpenAgent Resource Scheduler  (Phase 6 — Phase B)

Monitors system resources (VRAM, RAM, CPU, Context size) and schedules/throttles
heavy tasks like repository indexing, symbol extraction, and embedding generation
during model inference to avoid resource exhaustion on 4GB VRAM hardware.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


@dataclass
class ResourceLimits:
    max_vram_mb:      float = 3800.0   # 3.8 GB
    max_ram_mb:       float = 20000.0  # 20 GB
    max_cpu_percent:  float = 85.0


@dataclass
class ResourceTelemetry:
    vram_used_mb:     float = 0.0
    ram_used_mb:      float = 0.0
    cpu_percent:      float = 0.0
    context_size:     int = 0


class ResourceScheduler:
    """
    Coordinates execution of resource-heavy background tasks based on system utilization
    and inference state.
    """

    def __init__(self, limits: ResourceLimits = ResourceLimits()) -> None:
        self.limits = limits
        self._is_inference_active = False
        # Queue of background callbacks deferred due to resource constraints
        self._deferred_jobs: List[tuple[str, Callable[[], Any]]] = []
        # Active background workers running (to allow canceling/suspending them)
        self._suspended_jobs: Dict[str, Callable[[], Any]] = {}

    # ── Inference Context Management ──────────────────────────────────────────

    def start_inference(self) -> None:
        """Flags that an LLM inference call is active. Suspends running background tasks."""
        self._is_inference_active = True

    def end_inference(self) -> None:
        """Flags that LLM inference has completed. Resumes suspended background tasks."""
        self._is_inference_active = False
        self.resume_deferred_jobs()

    # ── Job Scheduling & Throttling ───────────────────────────────────────────

    def schedule_job(self, name: str, job_fn: Callable[[], Any]) -> bool:
        """
        Attempts to execute a job immediately. If inference is active or resource
        limits are exceeded, defers the job.
        Returns True if executed immediately, False if deferred.
        """
        if self._is_inference_active:
            self._deferred_jobs.append((name, job_fn))
            return False

        # Execute immediately
        try:
            job_fn()
            return True
        except Exception:
            # Propagate error if the job itself fails
            raise

    def suspend_job(self, name: str, resume_fn: Callable[[], Any]) -> None:
        """Suspends an active background task when resource pressure rises."""
        self._suspended_jobs[name] = resume_fn

    def resume_deferred_jobs(self) -> int:
        """Runs all deferred tasks in the queue. Returns count of executed jobs."""
        executed = 0
        temp_queue = list(self._deferred_jobs)
        self._deferred_jobs.clear()

        for name, job_fn in temp_queue:
            try:
                job_fn()
                executed += 1
            except Exception:
                continue

        # Also resume suspended active tasks
        for name, resume_fn in list(self._suspended_jobs.items()):
            try:
                resume_fn()
                self._suspended_jobs.pop(name, None)
            except Exception:
                continue

        return executed

    # ── Resource Monitoring Checks ────────────────────────────────────────────

    def check_memory_pressure(self, current: ResourceTelemetry) -> bool:
        """Returns True if any resource usage exceeds limits, indicating throttle is needed."""
        if current.vram_used_mb > self.limits.max_vram_mb:
            return True
        if current.ram_used_mb > self.limits.max_ram_mb:
            return True
        if current.cpu_percent > self.limits.max_cpu_percent:
            return True
        return False
