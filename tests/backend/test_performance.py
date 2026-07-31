"""
Unit tests for Phase I: PerformanceProfiler
"""

import pytest
from backend.performance_profiler import PerformanceProfiler, ProfileRecord


class TestPerformanceProfiler:

    def test_record_time_stats(self):
        profiler = PerformanceProfiler()
        
        profiler.record_time("indexing", 0.100)
        profiler.record_time("indexing", 0.200)
        
        rec = profiler.records["indexing"]
        assert rec.calls == 2
        assert rec.total_time == pytest.approx(0.300)
        assert rec.min_time == pytest.approx(0.100)
        assert rec.max_time == pytest.approx(0.200)
        assert rec.avg_time == pytest.approx(0.150)

    def test_generate_report_no_bottlenecks(self):
        profiler = PerformanceProfiler()
        profiler.record_time("planning", 0.020)
        
        report = profiler.generate_report()
        assert "# OpenAgent Subsystems Performance Profile" in report
        assert "No major performance bottlenecks detected" in report

    def test_generate_report_with_bottlenecks(self):
        profiler = PerformanceProfiler()
        # Simulate high latency operation
        profiler.record_time("expensive_inference", 1.500)
        
        report = profiler.generate_report()
        assert "expensive_inference" in report
        assert "Consider caching" in report
