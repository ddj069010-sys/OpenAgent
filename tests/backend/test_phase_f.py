"""
Unit tests for Phase F: PersistentMemory
"""

import pytest
from pathlib import Path
from backend.persistent_memory import PersistentMemory, MemoryItem


class TestPersistentMemory:

    def test_add_and_get_memory(self, tmp_path):
        mfile = tmp_path / "memory.json"
        mem = PersistentMemory(mfile)
        
        mem.add("arch_decision", "Use SQLite WAL mode", {"reason": "crash recovery"})
        mem.add("todo", "Refactor the context engine")
        
        decisions = mem.get_by_category("arch_decision")
        assert len(decisions) == 1
        assert decisions[0].content == "Use SQLite WAL mode"
        assert decisions[0].metadata.get("reason") == "crash recovery"

    def test_search_memory(self, tmp_path):
        mfile = tmp_path / "memory.json"
        mem = PersistentMemory(mfile)
        
        mem.add("known_bug", "VRAM leak in model switching")
        mem.add("prev_fix", "Reset LLM cache to avoid leak")
        
        results = mem.search("leak")
        assert len(results) == 2
        
        results_vram = mem.search("VRAM")
        assert len(results_vram) == 1
        assert "switching" in results_vram[0].content

    def test_persistence_across_instances(self, tmp_path):
        mfile = tmp_path / "memory.json"
        
        # Instance 1: write
        mem1 = PersistentMemory(mfile)
        mem1.add("convention", "Use tab-based indentation for frontend")
        
        # Instance 2: read
        mem2 = PersistentMemory(mfile)
        convs = mem2.get_by_category("convention")
        assert len(convs) == 1
        assert convs[0].content == "Use tab-based indentation for frontend"
