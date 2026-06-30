from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from contract4agents.cli import main

ROOT = Path(__file__).resolve().parents[2]


def test_cli_help_and_check() -> None:
    runner = CliRunner()

    help_result = runner.invoke(main, ["--help"])
    check_result = runner.invoke(main, ["check", str(ROOT / "examples" / "incident-command")])

    assert help_result.exit_code == 0
    assert "compile" in help_result.output
    assert check_result.exit_code == 0
    assert "passed" in check_result.output

    eval_help = runner.invoke(main, ["eval", "--help"])
    assert eval_help.exit_code == 0
    assert "fixture.json" in eval_help.output
    assert "Incident Command" not in eval_help.output


def test_cli_compile_docs_eval_monitor(tmp_path: Path) -> None:
    runner = CliRunner()
    fixture = ROOT / "examples" / "incident-command"
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(json.dumps({"type": "run.started", "timestamp": 1.0, "data": {"run_id": "ok"}}) + "\n")
    violation_trace_path = tmp_path / "violation.jsonl"
    violation_trace_path.write_text(
        json.dumps(
            {
                "type": "tool.completed",
                "timestamp": 1.0,
                "data": {"tool": "status_page.draft_update"},
            }
        )
        + "\n"
    )

    assert runner.invoke(main, ["compile", str(fixture), "--out", str(tmp_path / "build")]).exit_code == 0
    assert runner.invoke(main, ["docs-check", str(ROOT)]).exit_code == 0
    assert runner.invoke(main, ["monitor", str(fixture), "--trace", str(trace_path)]).exit_code == 0
    violation = runner.invoke(main, ["monitor", str(fixture), "--trace", str(violation_trace_path)])
    assert violation.exit_code != 0
    assert "status_update_requires_approval" in violation.output


def test_cli_monitor_reports_invalid_trace_json(tmp_path: Path) -> None:
    runner = CliRunner()
    fixture = ROOT / "examples" / "incident-command"
    trace_path = tmp_path / "bad.jsonl"
    trace_path.write_text("{bad\n")

    result = runner.invoke(main, ["monitor", str(fixture), "--trace", str(trace_path)])

    assert result.exit_code != 0
    assert "Invalid trace JSON" in result.output


def test_cli_eval_runs_fixture_json_project(contract_project_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["eval", str(contract_project_path)])

    assert result.exit_code == 0
    assert "Fixture eval passed: 12 starts" in result.output
