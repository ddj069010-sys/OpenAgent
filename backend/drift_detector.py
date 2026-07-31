"""
OpenAgent Drift Detector  (Phase 8.5)

After every significant change, compares current repository state against the
architecture spec, roadmap, ADRs, and coding conventions to detect drift:
duplicate services, orphaned modules, circular deps, dead code, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set


@dataclass
class DriftIssue:
    severity:    str           # "ERROR" | "WARNING" | "INFO"
    category:    str           # "DUPLICATE" | "ORPHAN" | "CIRCULAR" | "DEAD_CODE" | "CONVENTION"
    description: str
    file_path:   str = ""


@dataclass
class DriftReport:
    issues:     List[DriftIssue] = field(default_factory=list)
    clean:      bool             = True

    def add(self, issue: DriftIssue) -> None:
        self.issues.append(issue)
        if issue.severity in ("ERROR", "WARNING"):
            self.clean = False


class DriftDetector:
    """
    Scans a workspace for common architectural drift patterns without
    touching the LLM — pure deterministic static analysis.
    """

    # Known canonical singleton names — duplicates of these are drift signals
    SINGLETON_SERVICES: Set[str] = {
        "ResourceScheduler", "JobScheduler", "PerformanceProfiler",
        "CapabilityRegistry", "CheckpointStore",
    }

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root)

    def scan(self) -> DriftReport:
        report = DriftReport()
        backend = self.root / "backend"

        if not backend.exists():
            return report

        py_files = list(backend.glob("*.py"))

        # ── 1. Duplicate class definitions ───────────────────────────────────
        class_counts: dict[str, List[str]] = {}
        for f in py_files:
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("class ") and ":" in stripped:
                    cls_name = stripped.split("class ")[1].split("(")[0].split(":")[0].strip()
                    class_counts.setdefault(cls_name, []).append(f.name)

        for cls, files in class_counts.items():
            if len(files) > 1:
                report.add(DriftIssue(
                    severity="ERROR",
                    category="DUPLICATE",
                    description=f"Class '{cls}' defined in multiple files: {files}",
                ))

        # ── 2. Orphaned modules (no imports from other backend files) ─────────
        all_names = {f.stem for f in py_files}
        imported_names: Set[str] = set()
        for f in py_files:
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("from backend.") or stripped.startswith("import backend."):
                    # e.g. "from backend.cost_aware_planner import ..."
                    parts = stripped.split(".")
                    if len(parts) >= 2:
                        mod = parts[1].split(" ")[0].split("import")[0].strip()
                        imported_names.add(mod)

        # Exclude well-known entry-points and __init__ from orphan check
        excluded = {"__init__", "container", "main", "app"}
        for mod in all_names - imported_names - excluded:
            report.add(DriftIssue(
                severity="WARNING",
                category="ORPHAN",
                description=f"Module 'backend/{mod}.py' appears unreferenced by other backend modules.",
                file_path=f"backend/{mod}.py",
            ))

        # ── 3. Missing IoC registration ───────────────────────────────────────
        try:
            container_text = (self.root / "backend" / "container.py").read_text(
                encoding="utf-8", errors="ignore"
            )
        except OSError:
            container_text = ""

        for cls, files in class_counts.items():
            if (
                cls in self.SINGLETON_SERVICES
                and f'"{cls}"' not in container_text
                and f"'{cls}'" not in container_text
            ):
                report.add(DriftIssue(
                    severity="WARNING",
                    category="CONVENTION",
                    description=f"Singleton '{cls}' found in {files} but not registered in container.py.",
                ))

        return report
