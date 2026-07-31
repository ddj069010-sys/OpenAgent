"""
OpenAgent Background Job Scheduler  (Phase 6 — Phase C)

Manages background tasks with explicit priority, cancellation, retry limits,
suspension states, and dependency chains.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Dict, List, Optional


class JobState(StrEnum):
    PENDING   = "pending"
    RUNNING   = "running"
    PAUSED    = "paused"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id:           str
    fn:           Callable[[], Any]
    priority:     int = 5                   # 1 (highest) to 10 (lowest)
    depends_on:   List[str] = field(default_factory=list)
    max_retries:  int = 3
    retries:      int = 0
    state:        JobState = JobState.PENDING
    error:        Optional[str] = None
    result:       Any = None


class JobScheduler:
    """
    Executes background jobs, respecting priorities, dependencies, and execution states.
    """

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}

    def submit(self, job: Job) -> None:
        self._jobs[job.id] = job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job and job.state in (JobState.PENDING, JobState.RUNNING, JobState.PAUSED):
            job.state = JobState.CANCELLED
            return True
        return False

    def pause(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job and job.state == JobState.RUNNING:
            job.state = JobState.PAUSED
            return True
        return False

    def resume(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job and job.state == JobState.PAUSED:
            job.state = JobState.PENDING
            return True
        return False

    # ── Job Execution ─────────────────────────────────────────────────────────

    def step(self) -> int:
        """
        Executes the next ready job. A job is ready if its state is PENDING
        and all its dependencies are COMPLETED.
        Returns the number of jobs run in this step (0 or 1).
        """
        ready_jobs = self._get_ready_jobs()
        if not ready_jobs:
            return 0

        # Run the highest priority job (lowest priority value)
        job = ready_jobs[0]
        job.state = JobState.RUNNING

        try:
            job.result = job.fn()
            job.state = JobState.COMPLETED
        except Exception as e:
            job.retries += 1
            job.error = str(e)
            if job.retries < job.max_retries:
                job.state = JobState.PENDING  # Retry later
            else:
                job.state = JobState.FAILED

        return 1

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_ready_jobs(self) -> List[Job]:
        completed_ids = {
            jid for jid, j in self._jobs.items()
            if j.state == JobState.COMPLETED
        }
        
        ready = []
        for job in self._jobs.values():
            if job.state == JobState.PENDING:
                # Check dependencies
                deps_satisfied = all(dep_id in completed_ids for dep_id in job.depends_on)
                if deps_satisfied:
                    ready.append(job)
                    
        return sorted(ready, key=lambda j: j.priority)
