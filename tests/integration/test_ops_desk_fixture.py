from __future__ import annotations

from pathlib import Path

from contract4agents.monitor import MonitorRule, run_monitors
from contract4agents.runtime import TraceRecorder
from tests.fixtures.fixture_runner import run_fixture_project_sync


def test_ops_desk_fixture_local_end_to_end(contract_project_path: Path, tmp_path: Path) -> None:
    first_root = tmp_path / "first-run"
    first_report = run_fixture_project_sync(project_root=contract_project_path, run_root=first_root)

    assert first_report.passed
    assert first_report.cleaned
    assert first_root.exists()
    assert _remaining_files(first_root) == [Path("reports/report.json"), Path("reports/report.md")]
    assert len(first_report.starts) == 12
    assert any(item.skipped_semantic for item in first_report.starts)

    second_root = tmp_path / "second-run"
    second_report = run_fixture_project_sync(project_root=contract_project_path, run_root=second_root)

    assert second_report.passed
    assert second_report.cleaned
    assert second_root.exists()
    assert _remaining_files(second_root) == [Path("reports/report.json"), Path("reports/report.md")]
    assert [item.start_id for item in first_report.starts] == [item.start_id for item in second_report.starts]


def test_ops_desk_monitors_catch_approval_violation() -> None:
    trace = TraceRecorder()
    trace.record("tool.completed", agent="OpsDeskCoordinator", tool="billing.create_credit", result={"credit_id": 1})

    violations = run_monitors(
        [
            MonitorRule(
                "billing_credit_requires_approval",
                "OpsDeskCoordinator",
                "high",
                "trace.tool_called(billing.create_credit)",
                'trace.approval_granted("billing.create_credit")',
            )
        ],
        trace,
    )

    assert violations
    assert violations[0].severity == "high"


def _remaining_files(root: Path) -> list[Path]:
    return sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())
