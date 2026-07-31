"""
OpenAgent Engineering Rules Engine  (Phase 8.10)

Encodes architectural conventions as deterministic rules rather than
embedding them in prompts. Rules fire automatically before and after
every code modification, eliminating a class of LLM hallucination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class RuleTrigger:
    condition:   str           # human-readable description
    check:       Callable[[str], bool]  # receives the target file path
    actions:     List[str]     = field(default_factory=list)   # what must happen
    severity:    str           = "WARNING"   # "ERROR" | "WARNING" | "INFO"


@dataclass
class RuleViolation:
    rule_condition: str
    actions_needed: List[str]
    severity:       str


@dataclass
class RulesReport:
    violations: List[RuleViolation] = field(default_factory=list)
    passed:     bool                = True

    def add(self, v: RuleViolation) -> None:
        self.violations.append(v)
        if v.severity == "ERROR":
            self.passed = False


class RulesEngine:
    """
    Applies deterministic IF-THEN engineering rules to a file path and
    action context. Returns a RulesReport listing required follow-ups.

    Rules are encoded data — not prompt text — so they fire consistently
    regardless of which LLM is active.
    """

    def __init__(self) -> None:
        self._rules: List[RuleTrigger] = []
        self._register_defaults()

    # ── Rule Registration ─────────────────────────────────────────────────────

    def add_rule(self, rule: RuleTrigger) -> None:
        self._rules.append(rule)

    def _register_defaults(self) -> None:
        self._rules = [
            RuleTrigger(
                condition="Editing backend Python file",
                check=lambda f: f.endswith(".py") and "backend/" in f,
                actions=["Run backend test suite: pytest tests/backend/"],
                severity="ERROR",
            ),
            RuleTrigger(
                condition="Editing or adding an API route",
                check=lambda f: "route" in f or "api" in f,
                actions=["Update API documentation", "Check OpenAPI schema"],
                severity="WARNING",
            ),
            RuleTrigger(
                condition="Adding a new service class",
                check=lambda f: f.endswith(".py") and "backend/" in f,
                actions=[
                    "Register service in backend/container.py",
                    "Add unit tests under tests/backend/",
                ],
                severity="ERROR",
            ),
            RuleTrigger(
                condition="Editing configuration file",
                check=lambda f: "config/" in f or f.endswith((".yaml", ".yml", ".json")),
                actions=["Update config schema validation", "Verify Pydantic models"],
                severity="WARNING",
            ),
            RuleTrigger(
                condition="Changing model routing or LLM parameters",
                check=lambda f: "model" in f.lower() or "routing" in f.lower(),
                actions=[
                    "Update hardware_profile limits in project_state.json",
                    "Re-validate VRAM budget in CostAwarePlanner",
                ],
                severity="WARNING",
            ),
            RuleTrigger(
                condition="Adding a new feature module",
                check=lambda f: f.endswith(".py") and "backend/" in f,
                actions=[
                    "Update roadmap in project_state.json",
                    "Write Engineering Journal entry",
                ],
                severity="INFO",
            ),
            RuleTrigger(
                condition="Making an architectural change",
                check=lambda f: any(
                    kw in f for kw in
                    ["container", "cognitive", "planner", "director", "framework"]
                ),
                actions=["Create an ADR (Architecture Decision Record)"],
                severity="WARNING",
            ),
        ]

    # ── Evaluation ────────────────────────────────────────────────────────────

    def evaluate(self, file_path: str) -> RulesReport:
        """
        Evaluates all rules against the given file path.
        Returns a RulesReport with any triggered rule violations.
        """
        report = RulesReport()
        for rule in self._rules:
            try:
                if rule.check(file_path):
                    report.add(RuleViolation(
                        rule_condition=rule.condition,
                        actions_needed=rule.actions,
                        severity=rule.severity,
                    ))
            except Exception:
                pass
        return report

    def summarize(self, report: RulesReport) -> str:
        """Returns a human-readable checklist from a RulesReport."""
        if not report.violations:
            return "✓ No rule violations detected."
        lines = ["Required actions before proceeding:"]
        for v in report.violations:
            prefix = "🔴" if v.severity == "ERROR" else ("🟡" if v.severity == "WARNING" else "🔵")
            lines.append(f"  {prefix} [{v.rule_condition}]")
            for action in v.actions_needed:
                lines.append(f"      → {action}")
        return "\n".join(lines)
