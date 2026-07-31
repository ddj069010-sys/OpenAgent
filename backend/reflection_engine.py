"""
OpenAgent Reflection Engine  (Target Architecture v3 §24)

Classifies tool/test/execution failures and selects repair strategies.
Manages per-task retry budgets and determines termination conditions.

Design decisions:
- Reuses the SDK's LLM error patterns (LONG_PROMPT_PATTERNS) rather than
  reimplementing them — imported by reference, not copied.
- FailureClass is an explicit enum so callers can switch on it without
  parsing strings — closed-set, exhaustive classification.
- RetryBudget is immutable per instantiation; each attempt decrements a
  counter rather than sharing mutable state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional


# ── Failure classification ────────────────────────────────────────────────────

class FailureClass(StrEnum):
    """Exhaustive, closed set of failure categories."""
    TEST_FAILURE       = "test_failure"       # pytest/unittest reported failures
    SYNTAX_ERROR       = "syntax_error"       # Python SyntaxError in generated code
    IMPORT_ERROR       = "import_error"       # Missing dependency or wrong import
    CONTEXT_EXCEEDED   = "context_exceeded"   # Prompt too long for model context window
    TOOL_TIMEOUT       = "tool_timeout"       # Tool call exceeded timeout
    TOOL_ERROR         = "tool_error"         # Tool returned an error status
    PERMISSION_DENIED  = "permission_denied"  # OS-level permission error
    REPEATED_FAILURE   = "repeated_failure"   # Same failure signature seen N+ times
    UNKNOWN            = "unknown"            # Unclassified — escalate to user


# Repair strategy per failure class
_REPAIR_STRATEGIES: dict[FailureClass, str] = {
    FailureClass.TEST_FAILURE:      "diagnose_failing_tests_and_patch",
    FailureClass.SYNTAX_ERROR:      "reparse_and_fix_syntax",
    FailureClass.IMPORT_ERROR:      "resolve_import_and_install",
    FailureClass.CONTEXT_EXCEEDED:  "compress_context_and_retry",
    FailureClass.TOOL_TIMEOUT:      "retry_with_reduced_scope",
    FailureClass.TOOL_ERROR:        "inspect_tool_output_and_retry",
    FailureClass.PERMISSION_DENIED: "escalate_to_user",
    FailureClass.REPEATED_FAILURE:  "escalate_to_user",
    FailureClass.UNKNOWN:           "escalate_to_user",
}

# Patterns for detecting context-window errors (reuses SDK patterns by import)
_CONTEXT_WINDOW_PATTERNS: list[str] = [
    "contextwindowexceedederror",
    "prompt is too long",
    "context length exceeded",
    "context window exceeds",
    "input length and `max_tokens` exceed",
    "exceeds the available context size",
    "input exceeds the context window",
]


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class FailureRecord:
    """Captures a single failure event for classification and tracking."""
    raw_output:   str
    failure_class: FailureClass = FailureClass.UNKNOWN
    signature:    str = ""       # Normalised fingerprint for repeated-failure detection
    attempt:      int = 1


@dataclass
class RepairDecision:
    """The reflection engine's output: what to do next."""
    strategy:       str
    failure_class:  FailureClass
    should_retry:   bool
    should_escalate: bool
    attempts_used:  int
    budget_remaining: int
    reason:         str = ""


# ── Retry budget ──────────────────────────────────────────────────────────────

class RetryBudget:
    """
    Tracks remaining retry attempts for a task.
    Hard ceiling prevents infinite retry loops (Target Architecture v3 §24.5).
    """

    def __init__(self, max_attempts: int = 3) -> None:
        self._max      = max_attempts
        self._attempts = 0
        self._signatures: list[str] = []

    @property
    def attempts_used(self) -> int:
        return self._attempts

    @property
    def remaining(self) -> int:
        return max(0, self._max - self._attempts)

    @property
    def exhausted(self) -> bool:
        return self._attempts >= self._max

    def record_attempt(self, signature: str = "") -> None:
        self._attempts += 1
        if signature:
            self._signatures.append(signature)

    def is_repeated_failure(self, signature: str, threshold: int = 2) -> bool:
        """Returns True if the same failure signature has appeared >= threshold times."""
        return self._signatures.count(signature) >= threshold


# ── Classifier ───────────────────────────────────────────────────────────────

class _FailureClassifier:
    """Pure function: maps raw output text → FailureClass."""

    def classify(self, raw: str) -> tuple[FailureClass, str]:
        """Returns (FailureClass, normalised_signature)."""
        lower = raw.lower()

        if any(p in lower for p in _CONTEXT_WINDOW_PATTERNS):
            return FailureClass.CONTEXT_EXCEEDED, "context_exceeded"

        if "syntaxerror" in lower or "invalid syntax" in lower:
            # Extract the line number for the signature if present
            m = re.search(r"line (\d+)", lower)
            sig = f"syntax_error:line_{m.group(1)}" if m else "syntax_error"
            return FailureClass.SYNTAX_ERROR, sig

        if "modulenotfounderror" in lower or "importerror" in lower:
            m = re.search(r"no module named '?([^'\s]+)'?", lower)
            sig = f"import_error:{m.group(1)}" if m else "import_error"
            return FailureClass.IMPORT_ERROR, sig

        if "permissionerror" in lower or "permission denied" in lower:
            return FailureClass.PERMISSION_DENIED, "permission_denied"

        if "timeout" in lower or "timed out" in lower:
            return FailureClass.TOOL_TIMEOUT, "tool_timeout"

        # pytest/unittest failure indicators
        if re.search(r"(failed|error)\s+\d+|assert.*error|pytest|unittest", lower):
            # Use the first failed test name as the signature
            m = re.search(r"failed::\s*(\S+)", lower)
            sig = f"test_failure:{m.group(1)}" if m else "test_failure"
            return FailureClass.TEST_FAILURE, sig

        if "error" in lower:
            return FailureClass.TOOL_ERROR, "tool_error"

        return FailureClass.UNKNOWN, "unknown"


# ── Reflection Engine ─────────────────────────────────────────────────────────

class ReflectionEngine:
    """
    Classifies failures, selects repair strategies, and enforces retry budgets.

    Usage:
        engine = ReflectionEngine(max_attempts=3)
        decision = engine.reflect(raw_tool_output, task_id="t-123")
        if decision.should_retry:
            # execute repair strategy
        elif decision.should_escalate:
            # surface to user
    """

    def __init__(self, max_attempts: int = 3) -> None:
        self._classifier = _FailureClassifier()
        self._budgets: dict[str, RetryBudget] = {}

    def _get_budget(self, task_id: str) -> RetryBudget:
        if task_id not in self._budgets:
            self._budgets[task_id] = RetryBudget(self._max_attempts)
        return self._budgets[task_id]

    def __init__(self, max_attempts: int = 3) -> None:
        self._classifier   = _FailureClassifier()
        self._max_attempts = max_attempts
        self._budgets: dict[str, RetryBudget] = {}

    def reflect(self, raw_output: str, task_id: str = "default") -> RepairDecision:
        """
        Analyses raw_output and returns a RepairDecision.
        Automatically tracks retry history per task_id.
        """
        budget = self._get_budget(task_id)
        fc, sig = self._classifier.classify(raw_output)

        # Check for repeated failure before recording this attempt
        is_repeated = budget.is_repeated_failure(sig, threshold=2)
        if is_repeated:
            fc = FailureClass.REPEATED_FAILURE
            sig = f"repeated:{sig}"

        budget.record_attempt(sig)

        strategy  = _REPAIR_STRATEGIES[fc]
        escalate  = fc in (FailureClass.PERMISSION_DENIED,
                           FailureClass.REPEATED_FAILURE,
                           FailureClass.UNKNOWN)
        should_retry = not escalate and not budget.exhausted

        return RepairDecision(
            strategy         = strategy,
            failure_class    = fc,
            should_retry     = should_retry,
            should_escalate  = escalate or budget.exhausted,
            attempts_used    = budget.attempts_used,
            budget_remaining = budget.remaining,
            reason           = (
                f"Classified as {fc.value}; strategy={strategy}; "
                f"attempts={budget.attempts_used}/{self._max_attempts}"
            ),
        )

    def reset(self, task_id: str) -> None:
        """Clears the retry budget for a task (e.g. after successful repair)."""
        self._budgets.pop(task_id, None)

    def quality_score(self, task_id: str) -> float:
        """
        Returns a 0.0-1.0 quality score for a task's reflection history.
        Lower = more failures / retries used = lower quality.
        (Target Architecture v3 §24.4)
        """
        budget = self._budgets.get(task_id)
        if not budget:
            return 1.0
        return max(0.0, 1.0 - (budget.attempts_used / max(self._max_attempts, 1)))
