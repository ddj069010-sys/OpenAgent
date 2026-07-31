"""
OpenAgent Task Planner  (Target Architecture v3 §23)

Decomposes a goal string into a DAG of SubTasks with dependency tracking,
priority ordering, and an execution queue integrated with TaskStateMachine.

Design decisions:
- DAG is stored as an adjacency list (deps: set[str]) — lightweight, no
  external graph library required. Topological sort uses Kahn's algorithm.
- Each SubTask has its own TaskStateMachine instance — state is local to
  the node, not shared via a global mutable structure.
- PlannedTask is the external representation (serialisable); SubTask is the
  runtime node with its state machine.
- The Planner intentionally does NOT call the LLM — it receives a plan
  structure (list of PlannedTask) and wires up the DAG. LLM-based planning
  (goal → plan) is the responsibility of the agent layer above.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from backend.task_state_machine import TaskState, TaskStateMachine, TERMINAL_STATES


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class PlannedTask:
    """
    Serialisable representation of a task node — produced by the agent/LLM
    and consumed by the Planner.
    """
    description: str
    priority:    int = 5           # 1 (highest) → 10 (lowest)
    depends_on:  list[str] = field(default_factory=list)  # IDs of prerequisite tasks
    id:          str = field(default_factory=lambda: uuid.uuid4().hex[:8])


@dataclass
class SubTask:
    """Runtime task node with state machine and dependency tracking."""
    id:          str
    description: str
    priority:    int
    deps:        set[str] = field(default_factory=set)   # IDs this task waits on
    state_machine: TaskStateMachine = field(init=False)
    result:      Optional[str] = None

    def __post_init__(self) -> None:
        self.state_machine = TaskStateMachine(task_id=self.id)

    @property
    def state(self) -> TaskState:
        return self.state_machine.state

    @property
    def is_terminal(self) -> bool:
        return self.state_machine.is_terminal


class CyclicDependencyError(ValueError):
    """Raised when the task DAG contains a cycle."""


# ── Task Planner ──────────────────────────────────────────────────────────────

class TaskPlanner:
    """
    Converts a list of PlannedTask nodes into a runnable, dependency-ordered
    execution queue.

    Usage:
        planner = TaskPlanner()
        queue   = planner.build(planned_tasks)    # returns ordered SubTask list
        ready   = planner.ready_tasks()            # tasks with no pending deps
        planner.mark_complete("task-id")           # advance state + unblock dependents
    """

    def __init__(self) -> None:
        self._tasks: dict[str, SubTask] = {}

    # ── Build ──────────────────────────────────────────────────────────────

    def build(self, planned: list[PlannedTask]) -> list[SubTask]:
        """
        Parses PlannedTask list into a validated DAG and returns tasks in
        topological (dependency-respecting) execution order.

        Raises CyclicDependencyError if a cycle is detected.
        """
        self._tasks.clear()

        # Create runtime nodes
        for p in planned:
            self._tasks[p.id] = SubTask(
                id=p.id,
                description=p.description,
                priority=p.priority,
                deps=set(p.depends_on),
            )

        ordered = self._topological_sort()
        return ordered

    def ready_tasks(self) -> list[SubTask]:
        """
        Returns all tasks currently in QUEUED state whose dependencies are
        all COMPLETED — i.e. safe to start now.
        Sorted by priority ascending (1 = highest).
        """
        completed = {
            tid for tid, t in self._tasks.items()
            if t.state == TaskState.COMPLETED
        }
        ready = [
            t for t in self._tasks.values()
            if t.state == TaskState.QUEUED
            and t.deps.issubset(completed)
        ]
        return sorted(ready, key=lambda t: t.priority)

    def mark_complete(self, task_id: str) -> None:
        """Transitions a task to COMPLETED, unblocking its dependents."""
        task = self._tasks[task_id]
        if not task.is_terminal:
            task.state_machine.transition(TaskState.TESTING)
            task.state_machine.transition(TaskState.COMPLETED)

    def mark_failed(self, task_id: str) -> None:
        """Transitions a task to FAILED."""
        task = self._tasks[task_id]
        if not task.is_terminal:
            if task.state == TaskState.QUEUED:
                task.state_machine.transition(TaskState.PLANNING)
            task.state_machine.transition(TaskState.FAILED)

    def transition(self, task_id: str, next_state: TaskState) -> None:
        """Manually advances a task's state machine."""
        self._tasks[task_id].state_machine.transition(next_state)

    def get(self, task_id: str) -> Optional[SubTask]:
        return self._tasks.get(task_id)

    def all_tasks(self) -> list[SubTask]:
        return list(self._tasks.values())

    def is_complete(self) -> bool:
        """Returns True when every task has reached a terminal state."""
        return all(t.is_terminal for t in self._tasks.values())

    def summary(self) -> dict:
        """Returns a compact plan status summary."""
        by_state: dict[str, int] = {}
        for t in self._tasks.values():
            by_state[t.state.value] = by_state.get(t.state.value, 0) + 1
        return {
            "total":    len(self._tasks),
            "by_state": by_state,
            "complete": self.is_complete(),
        }

    # ── Internal ──────────────────────────────────────────────────────────

    def _topological_sort(self) -> list[SubTask]:
        """
        Kahn's algorithm — O(V+E). Raises CyclicDependencyError if any cycle exists.
        """
        in_degree: dict[str, int] = {tid: 0 for tid in self._tasks}
        for task in self._tasks.values():
            for dep in task.deps:
                if dep not in self._tasks:
                    raise ValueError(
                        f"Task '{task.id}' depends on unknown task '{dep}'"
                    )
                in_degree[task.id] = in_degree.get(task.id, 0) + 1

        # Start with zero-in-degree nodes, sorted by priority
        queue = sorted(
            [tid for tid, deg in in_degree.items() if deg == 0],
            key=lambda tid: self._tasks[tid].priority,
        )
        ordered: list[SubTask] = []

        while queue:
            tid = queue.pop(0)
            ordered.append(self._tasks[tid])

            # Reduce in-degree of dependents
            for t in self._tasks.values():
                if tid in t.deps:
                    in_degree[t.id] -= 1
                    if in_degree[t.id] == 0:
                        queue.append(t.id)
                        queue.sort(key=lambda x: self._tasks[x].priority)

        if len(ordered) != len(self._tasks):
            raise CyclicDependencyError(
                "Task DAG contains a cycle — cannot determine execution order."
            )
        return ordered
