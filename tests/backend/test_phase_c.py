"""
Unit tests for Phase C: JobScheduler
"""

import pytest
from backend.job_scheduler import JobScheduler, Job, JobState


class TestJobScheduler:

    def test_single_job_execution(self):
        sched = JobScheduler()
        
        runs = []
        job = Job(id="j1", fn=lambda: runs.append(1))
        sched.submit(job)
        
        executed = sched.step()
        assert executed == 1
        assert len(runs) == 1
        assert job.state == JobState.COMPLETED

    def test_priority_ordering(self):
        sched = JobScheduler()
        
        order = []
        job1 = Job(id="j1", fn=lambda: order.append("j1"), priority=10) # Low priority
        job2 = Job(id="j2", fn=lambda: order.append("j2"), priority=1)  # High priority
        
        sched.submit(job1)
        sched.submit(job2)
        
        sched.step()
        assert order == ["j2"]
        
        sched.step()
        assert order == ["j2", "j1"]

    def test_dependency_chain(self):
        sched = JobScheduler()
        
        order = []
        # Job2 depends on Job1
        job1 = Job(id="j1", fn=lambda: order.append("j1"))
        job2 = Job(id="j2", fn=lambda: order.append("j2"), depends_on=["j1"])
        
        sched.submit(job1)
        sched.submit(job2)
        
        # Step should run Job1 first since Job2's dependency is not completed yet
        sched.step()
        assert order == ["j1"]
        
        # Next step should run Job2
        sched.step()
        assert order == ["j1", "j2"]

    def test_job_retries_on_failure(self):
        sched = JobScheduler()
        
        attempts = 0
        def failing_fn():
            nonlocal attempts
            attempts += 1
            raise ValueError("Intentional failure")
            
        job = Job(id="j1", fn=failing_fn, max_retries=2)
        sched.submit(job)
        
        # Attempt 1
        sched.step()
        assert attempts == 1
        assert job.state == JobState.PENDING  # Retries not exhausted yet
        
        # Attempt 2
        sched.step()
        assert attempts == 2
        assert job.state == JobState.FAILED   # Retries exhausted

    def test_pause_resume_cancel(self):
        sched = JobScheduler()
        
        job = Job(id="j1", fn=lambda: None)
        sched.submit(job)
        
        # Cancel pending job
        sched.cancel("j1")
        assert job.state == JobState.CANCELLED
        
        # Step should not run cancelled job
        assert sched.step() == 0
