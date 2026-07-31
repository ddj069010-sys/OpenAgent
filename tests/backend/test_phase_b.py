"""
Unit tests for Phase B: ResourceScheduler
"""

import pytest
from backend.resource_scheduler import ResourceScheduler, ResourceLimits, ResourceTelemetry


class TestResourceScheduler:

    def test_schedule_job_runs_immediately_when_idle(self):
        scheduler = ResourceScheduler()
        
        runs = []
        def job():
            runs.append(1)
            
        success = scheduler.schedule_job("test_job", job)
        assert success is True
        assert len(runs) == 1

    def test_schedule_job_defers_during_inference(self):
        scheduler = ResourceScheduler()
        scheduler.start_inference()
        
        runs = []
        def job():
            runs.append(1)
            
        success = scheduler.schedule_job("test_job", job)
        assert success is False
        assert len(runs) == 0
        
        # End inference -> should be able to resume
        scheduler.end_inference()
        assert len(runs) == 1

    def test_memory_pressure_checks(self):
        limits = ResourceLimits(max_vram_mb=3000.0, max_ram_mb=16000.0, max_cpu_percent=80.0)
        scheduler = ResourceScheduler(limits)
        
        # Under limits
        telemetry_ok = ResourceTelemetry(vram_used_mb=2000.0, ram_used_mb=12000.0, cpu_percent=50.0)
        assert scheduler.check_memory_pressure(telemetry_ok) is False
        
        # Over limits: VRAM
        telemetry_bad_vram = ResourceTelemetry(vram_used_mb=3500.0, ram_used_mb=12000.0, cpu_percent=50.0)
        assert scheduler.check_memory_pressure(telemetry_bad_vram) is True

    def test_suspend_and_resume_jobs(self):
        scheduler = ResourceScheduler()
        
        resumed = []
        def resume_callback():
            resumed.append("job1")
            
        scheduler.suspend_job("job1", resume_callback)
        assert len(resumed) == 0
        
        # Resume trigger
        scheduler.resume_deferred_jobs()
        assert len(resumed) == 1
