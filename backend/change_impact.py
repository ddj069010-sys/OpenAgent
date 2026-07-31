"""
OpenAgent Change Impact Analyzer  (Phase 8.6)

Before any file is edited, estimates which modules depend on it,
which tests may fail, which docs need updating, and which APIs are affected.
Acts as an automated senior developer pre-review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set


@dataclass
class ChangeImpact:
    target_file:        str
    dependent_modules:  List[str] = field(default_factory=list)
    tests_at_risk:      List[str] = field(default_factory=list)
    docs_to_update:     List[str] = field(default_factory=list)
    apis_affected:      List[str] = field(default_factory=list)
    config_files:       List[str] = field(default_factory=list)
    risk_level:         str       = "LOW"    # "LOW" | "MEDIUM" | "HIGH"
    summary:            str       = ""


class ChangeImpactAnalyzer:
    """
    Static-analysis impact estimator. Scans import graphs and naming
    conventions — no LLM required.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root)

    def analyze(self, target_file: str | Path) -> ChangeImpact:
        """
        Returns a ChangeImpact describing downstream consequences of editing
        the given file.
        """
        target  = Path(target_file)
        stem    = target.stem                # e.g. "resource_scheduler"
        impact  = ChangeImpact(target_file=str(target))

        backend_dir = self.root / "backend"
        tests_dir   = self.root / "tests"
        docs_dir    = self.root / "Agent_Architecture"

        # ── 1. Dependent backend modules ──────────────────────────────────────
        if backend_dir.exists():
            for py in backend_dir.glob("*.py"):
                if py.resolve() == target.resolve():
                    continue
                try:
                    text = py.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if stem in text:
                    impact.dependent_modules.append(py.name)

        # ── 2. Tests at risk ──────────────────────────────────────────────────
        if tests_dir.exists():
            for test_py in tests_dir.rglob("test_*.py"):
                try:
                    text = test_py.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if stem in text:
                    impact.tests_at_risk.append(
                        str(test_py.relative_to(self.root))
                    )

        # ── 3. Documentation to update ────────────────────────────────────────
        if docs_dir.exists():
            for md in docs_dir.rglob("*.md"):
                try:
                    text = md.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if stem in text:
                    impact.docs_to_update.append(
                        str(md.relative_to(self.root))
                    )

        # ── 4. API surface detection ──────────────────────────────────────────
        try:
            target_text = target.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            target_text = ""

        impact.apis_affected = [
            line.strip()
            for line in target_text.splitlines()
            if line.strip().startswith("def ") and not line.strip().startswith("def _")
        ]

        # ── 5. Config files ───────────────────────────────────────────────────
        config_dir = self.root / "config"
        if config_dir.exists():
            for cfg in config_dir.rglob("*"):
                if cfg.is_file():
                    try:
                        text = cfg.read_text(encoding="utf-8", errors="ignore")
                        if stem in text:
                            impact.config_files.append(
                                str(cfg.relative_to(self.root))
                            )
                    except OSError:
                        continue

        # ── 6. Risk scoring ───────────────────────────────────────────────────
        dep_count = len(impact.dependent_modules)
        test_count = len(impact.tests_at_risk)
        if dep_count >= 5 or test_count >= 10:
            impact.risk_level = "HIGH"
        elif dep_count >= 2 or test_count >= 3:
            impact.risk_level = "MEDIUM"

        impact.summary = (
            f"Editing '{target.name}' affects {dep_count} modules, "
            f"{test_count} test files, "
            f"{len(impact.apis_affected)} public API endpoints. "
            f"Risk: {impact.risk_level}."
        )
        return impact
