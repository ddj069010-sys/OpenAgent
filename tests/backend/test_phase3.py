"""
Unit tests for TaskStateMachine, CheckpointStore, and RepositoryIndex
(Target Architecture v3 §20, §21, §22)
"""

import pytest
import tempfile
from pathlib import Path

from backend.task_state_machine import (
    TaskState, TaskStateMachine, InvalidTransitionError, TERMINAL_STATES
)
from backend.checkpoint_store import CheckpointStore, TaskCheckpoint
from backend.repository_index import RepositoryIndex


# ═══════════════════════════════════════════════════════════════════════════
# TaskStateMachine tests
# ═══════════════════════════════════════════════════════════════════════════

class TestTaskStateMachine:

    def test_initial_state_is_queued(self):
        sm = TaskStateMachine("t1")
        assert sm.state == TaskState.QUEUED

    def test_valid_happy_path(self):
        sm = TaskStateMachine("t2")
        sm.transition(TaskState.PLANNING)
        sm.transition(TaskState.RUNNING)
        sm.transition(TaskState.TESTING)
        sm.transition(TaskState.COMPLETED)
        assert sm.state == TaskState.COMPLETED
        assert sm.is_terminal

    def test_repair_loop(self):
        sm = TaskStateMachine("t3")
        sm.transition(TaskState.PLANNING)
        sm.transition(TaskState.RUNNING)
        sm.transition(TaskState.TESTING)
        sm.transition(TaskState.REPAIRING)
        sm.transition(TaskState.TESTING)
        sm.transition(TaskState.COMPLETED)
        assert sm.state == TaskState.COMPLETED

    def test_invalid_transition_raises(self):
        sm = TaskStateMachine("t4")
        with pytest.raises(InvalidTransitionError):
            sm.transition(TaskState.COMPLETED)   # QUEUED → COMPLETED not allowed

    def test_no_transition_from_terminal(self):
        sm = TaskStateMachine("t5")
        sm.transition(TaskState.CANCELLED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(TaskState.PLANNING)

    def test_can_transition_returns_false_for_invalid(self):
        sm = TaskStateMachine("t6")
        assert sm.can_transition(TaskState.COMPLETED) is False
        assert sm.can_transition(TaskState.PLANNING) is True

    def test_history_records_transitions(self):
        sm = TaskStateMachine("t7")
        sm.transition(TaskState.PLANNING)
        sm.transition(TaskState.RUNNING)
        history = sm.history()
        assert history == [
            (TaskState.QUEUED, TaskState.PLANNING),
            (TaskState.PLANNING, TaskState.RUNNING),
        ]

    def test_terminal_states(self):
        for terminal in [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED]:
            sm = TaskStateMachine("t_term")
            # Navigate to terminal via cancel or fail
            sm.transition(TaskState.CANCELLED if terminal == TaskState.CANCELLED
                          else TaskState.PLANNING)
            if not sm.is_terminal:
                sm.transition(TaskState.RUNNING)
                sm.transition(TaskState.FAILED if terminal == TaskState.FAILED
                              else TaskState.TESTING)
                if not sm.is_terminal:
                    sm.transition(TaskState.COMPLETED)
            assert sm.is_terminal


# ═══════════════════════════════════════════════════════════════════════════
# CheckpointStore tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCheckpointStore:

    def _make_store(self) -> CheckpointStore:
        tmp = tempfile.mktemp(suffix=".db")
        return CheckpointStore(Path(tmp))

    def _make_checkpoint(self, task_id: str = "task-1", state: str = "running") -> TaskCheckpoint:
        return TaskCheckpoint(
            task_id=task_id,
            state=state,
            goal="Fix the bug in auth module",
            plan=[{"step": 1, "action": "locate_symbol"}],
            tool_outputs=[{"tool": "bash", "output": "OK"}],
            context_summary="Auth module has a null pointer in login()",
            terminal_history=["git status", "pytest"],
        )

    def test_save_and_restore(self):
        store = self._make_store()
        cp = self._make_checkpoint()
        store.save(cp)
        restored = store.restore_latest("task-1")
        assert restored is not None
        assert restored.task_id == "task-1"
        assert restored.goal == cp.goal

    def test_restore_returns_none_for_unknown_task(self):
        store = self._make_store()
        assert store.restore_latest("nonexistent") is None

    def test_content_hash_validation(self):
        cp = self._make_checkpoint()
        h = cp.content_hash
        assert len(h) == 64  # SHA-256 hex

    def test_interrupted_tasks_detection(self):
        store = self._make_store()
        store.save(self._make_checkpoint("running-task", "running"))
        store.save(self._make_checkpoint("done-task", "completed"))
        interrupted = store.list_interrupted_tasks()
        task_ids = [t["task_id"] for t in interrupted]
        assert "running-task" in task_ids
        assert "done-task" not in task_ids

    def test_restore_returns_latest(self):
        store = self._make_store()
        cp1 = self._make_checkpoint("t", "planning")
        cp2 = self._make_checkpoint("t", "running")
        store.save(cp1)
        store.save(cp2)
        latest = store.restore_latest("t")
        assert latest.state == "running"

    def test_integrity_check_passes_on_fresh_db(self):
        store = self._make_store()
        assert store.validate() is True


# ═══════════════════════════════════════════════════════════════════════════
# RepositoryIndex tests
# ═══════════════════════════════════════════════════════════════════════════

class TestRepositoryIndex:

    def _write_py(self, tmp: Path, name: str, code: str) -> Path:
        p = tmp / name
        p.write_text(code)
        return p

    def test_indexes_functions_and_classes(self, tmp_path):
        self._write_py(tmp_path, "a.py", """
class Foo:
    def bar(self): pass

def standalone(): pass
""")
        idx = RepositoryIndex(tmp_path)
        idx.index_workspace()
        symbols = idx.symbols_in(str(tmp_path / "a.py"))
        names = [s.name for s in symbols]
        assert "Foo" in names
        assert "bar" in names
        assert "standalone" in names

    def test_symbol_kinds(self, tmp_path):
        self._write_py(tmp_path, "b.py", """
class MyClass:
    def method(self): pass
def func(): pass
""")
        idx = RepositoryIndex(tmp_path)
        idx.index_workspace()
        symbols = {s.name: s.kind for s in idx.symbols_in(str(tmp_path / "b.py"))}
        assert symbols["MyClass"] == "class"
        assert symbols["method"] == "method"
        assert symbols["func"] == "function"

    def test_import_graph(self, tmp_path):
        self._write_py(tmp_path, "c.py", "import os\nfrom pathlib import Path\n")
        idx = RepositoryIndex(tmp_path)
        idx.index_workspace()
        imports = idx.imports_of(str(tmp_path / "c.py"))
        assert "os" in imports
        assert "pathlib" in imports

    def test_incremental_no_reindex_on_unchanged(self, tmp_path):
        self._write_py(tmp_path, "d.py", "def f(): pass\n")
        idx = RepositoryIndex(tmp_path)
        first = idx.index_workspace()
        second = idx.index_workspace()
        assert first == 1
        assert second == 0   # nothing changed → no re-index

    def test_find_symbol_across_files(self, tmp_path):
        self._write_py(tmp_path, "e.py", "def shared(): pass\n")
        self._write_py(tmp_path, "f.py", "def shared(): pass\n")
        idx = RepositoryIndex(tmp_path)
        idx.index_workspace()
        results = idx.find_symbol("shared")
        assert len(results) == 2

    def test_summary_structure(self, tmp_path):
        self._write_py(tmp_path, "g.py", "def x(): pass\n")
        idx = RepositoryIndex(tmp_path)
        idx.index_workspace()
        s = idx.summary()
        assert s["indexed_files"] == 1
        assert s["total_symbols"] >= 1

    def test_skips_syntax_errors_gracefully(self, tmp_path):
        bad = tmp_path / "bad.py"
        bad.write_text("def (: broken syntax !!!")
        idx = RepositoryIndex(tmp_path)
        idx.index_workspace()
        # File is attempted but stores no symbols (SyntaxError caught silently)
        assert idx.symbols_in(str(bad)) == []

    def test_callers_of(self, tmp_path):
        self._write_py(tmp_path, "caller.py", "def run():\n    connect()\n")
        idx = RepositoryIndex(tmp_path)
        idx.index_workspace()
        callers = idx.callers_of("connect")
        assert str(tmp_path / "caller.py") in callers
