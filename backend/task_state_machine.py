"""
OpenAgent Task State Machine  (Target Architecture v3 §20, §21)

Defines explicit task lifecycle states and valid transitions.
Every agent task MUST move through these states — no implicit state drift.

Extends the SDK's ConversationExecutionStatus with engineering-phase granularity
without duplicating the SDK's own model.
"""

from __future__ import annotations

from enum import StrEnum
from typing import FrozenSet


class TaskState(StrEnum):
    """
    Granular task lifecycle states for OpenAgent autonomous engineering tasks.

    Maps onto the SDK's ConversationExecutionStatus:
      QUEUED / PLANNING / WAITING → IDLE
      RUNNING / TESTING / REPAIRING → RUNNING
      REVIEW → WAITING_FOR_CONFIRMATION
      COMPLETED → FINISHED
      FAILED / CANCELLED → ERROR
    """

    QUEUED    = "queued"     # Accepted; waiting for execution slot
    PLANNING  = "planning"   # Task DAG being constructed by planner
    WAITING   = "waiting"    # Blocked on a dependency or human approval
    RUNNING   = "running"    # Active tool execution in progress
    TESTING   = "testing"    # Validation / test-runner phase
    REPAIRING = "repairing"  # Reflection loop: diagnosing and patching failures
    REVIEW    = "review"     # Needs human review before proceeding
    COMPLETED = "completed"  # All subtasks done; outputs verified
    FAILED    = "failed"     # Exhausted retries or unrecoverable error
    CANCELLED = "cancelled"  # Explicitly stopped by user or policy


# ── Valid transitions ────────────────────────────────────────────────────────
# Key = current state, Value = set of states reachable from it.
# Attempting any other transition raises InvalidTransitionError.

VALID_TRANSITIONS: dict[TaskState, FrozenSet[TaskState]] = {
    TaskState.QUEUED:    frozenset({TaskState.PLANNING, TaskState.CANCELLED}),
    TaskState.PLANNING:  frozenset({TaskState.RUNNING, TaskState.WAITING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.WAITING:   frozenset({TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.RUNNING:   frozenset({TaskState.TESTING, TaskState.REPAIRING, TaskState.WAITING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.TESTING:   frozenset({TaskState.COMPLETED, TaskState.REPAIRING, TaskState.REVIEW, TaskState.FAILED}),
    TaskState.REPAIRING: frozenset({TaskState.TESTING, TaskState.REVIEW, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.REVIEW:    frozenset({TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.COMPLETED: frozenset(),   # terminal
    TaskState.FAILED:    frozenset(),   # terminal
    TaskState.CANCELLED: frozenset(),   # terminal
}

TERMINAL_STATES: FrozenSet[TaskState] = frozenset({
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELLED,
})


class InvalidTransitionError(RuntimeError):
    """Raised when a state transition violates the defined state machine."""


class TaskStateMachine:
    """
    Enforces valid state transitions for a single task.

    Usage:
        sm = TaskStateMachine(task_id="abc123")
        sm.transition(TaskState.PLANNING)
        sm.transition(TaskState.RUNNING)
        sm.transition(TaskState.TESTING)
        sm.transition(TaskState.COMPLETED)
    """

    def __init__(self, task_id: str, initial: TaskState = TaskState.QUEUED) -> None:
        self.task_id  = task_id
        self._state   = initial
        self._history: list[tuple[TaskState, TaskState]] = []

    @property
    def state(self) -> TaskState:
        return self._state

    @property
    def is_terminal(self) -> bool:
        return self._state in TERMINAL_STATES

    def transition(self, next_state: TaskState) -> None:
        """
        Moves the state machine to next_state.
        Raises InvalidTransitionError if the transition is not allowed.
        """
        allowed = VALID_TRANSITIONS.get(self._state, frozenset())
        if next_state not in allowed:
            raise InvalidTransitionError(
                f"Task {self.task_id}: cannot transition {self._state!r} → {next_state!r}. "
                f"Allowed: {sorted(allowed)}"
            )
        self._history.append((self._state, next_state))
        self._state = next_state

    def can_transition(self, next_state: TaskState) -> bool:
        """Returns True if the transition would be valid without raising."""
        return next_state in VALID_TRANSITIONS.get(self._state, frozenset())

    def history(self) -> list[tuple[TaskState, TaskState]]:
        """Returns the full transition history as (from, to) pairs."""
        return list(self._history)
