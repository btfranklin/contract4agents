"""Fixture report writing and generated artifact cleanup."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from contract4agents.fixtures._models import GENERATED_ARTIFACT_DIRS, FixtureReport


def write_report(report: FixtureReport, run_root: Path) -> None:
    (run_root / "reports").mkdir(parents=True, exist_ok=True)
    (run_root / "reports" / "report.json").write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    lines = [
        "# Contract4Agents Fixture Report",
        "",
        f"- Mode: {report.mode}",
        f"- Starts: {len(report.starts)}",
        f"- Passed: {report.passed}",
        f"- Generated artifacts cleaned: {report.cleaned}",
        "",
        "## Artifact Checks",
        "",
    ]
    for check in report.artifact_checks:
        lines.append(f"- PASS {check}")
    lines.extend(
        [
            "",
            "## Starts",
            "",
            "| Start | Status | Attempts | Failures | Assertion Failures | "
            "Monitor Violations | Skipped Semantic Checks |",
            "| --- | --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for item in report.starts:
        status = "PASS" if item.passed else "FAIL"
        lines.append(
            "| "
            f"{item.start_id} | "
            f"{status} | "
            f"{item.attempts} | "
            f"{_cell(item.failures)} | "
            f"{_cell(item.assertion_failures)} | "
            f"{_cell(item.monitor_violations)} | "
            f"{_cell(item.skipped_semantic)} |"
        )
    retry_errors = {item.start_id: item.retry_errors for item in report.starts if item.retry_errors}
    if retry_errors:
        lines.extend(["", "## Retry Errors", ""])
        for start_id, errors in retry_errors.items():
            lines.append(f"- {start_id}: {_cell(errors)}")
    (run_root / "reports" / "report.md").write_text("\n".join(lines) + "\n")


def cleanup_generated_artifacts(run_root: Path) -> bool:
    for name in GENERATED_ARTIFACT_DIRS:
        path = run_root / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    return True


def _cell(values: list[str]) -> str:
    if not values:
        return "-"
    return "<br>".join(value.replace("|", "\\|") for value in values)


__all__ = ["cleanup_generated_artifacts", "write_report"]
