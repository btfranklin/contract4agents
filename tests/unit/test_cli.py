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
    trace_path.write_text(
        json.dumps(
            _trace_event(
                event_id="evt-1",
                event_type="agent.completed",
                timestamp=1.0,
                agent="IncidentCommander",
            )
        )
        + "\n"
    )
    violation_trace_path = tmp_path / "violation.jsonl"
    violation_trace_path.write_text(
        json.dumps(
            _trace_event(
                event_id="evt-2",
                event_type="tool.completed",
                timestamp=1.0,
                tool="status_page.draft_update",
            )
        )
        + "\n"
    )

    assert runner.invoke(main, ["compile", str(fixture), "--out", str(tmp_path / "build")]).exit_code == 0
    assert runner.invoke(main, ["docs-check", str(ROOT)]).exit_code == 0
    assert runner.invoke(main, ["monitor", str(fixture), "--trace", str(trace_path)]).exit_code == 0
    violation = runner.invoke(main, ["monitor", str(fixture), "--trace", str(violation_trace_path)])
    assert violation.exit_code != 0
    assert "status_update_requires_approval" in violation.output

    multi_run_trace_path = tmp_path / "multi-run.jsonl"
    multi_run_trace_path.write_text(
        json.dumps(
            _trace_event(
                run_id="run-tool",
                event_id="evt-3",
                event_type="tool.completed",
                timestamp=1.0,
                tool="status_page.draft_update",
            )
        )
        + "\n"
        + json.dumps(
            _trace_event(
                run_id="run-approval",
                event_id="evt-4",
                event_type="approval.completed",
                timestamp=2.0,
                tool="status_page.draft_update",
                approved=True,
            )
        )
        + "\n"
    )
    multi_run_missing_scope = runner.invoke(main, ["monitor", str(fixture), "--trace", str(multi_run_trace_path)])
    assert multi_run_missing_scope.exit_code != 0
    assert "multiple run_id" in multi_run_missing_scope.output

    scoped_pass = runner.invoke(
        main,
        ["monitor", str(fixture), "--trace", str(multi_run_trace_path), "--run-id", "run-approval"],
    )
    assert scoped_pass.exit_code == 0


def test_cli_pydantic_import_gate(tmp_path: Path) -> None:
    runner = CliRunner()
    fixture = ROOT / "tests" / "fixtures" / "contract_projects" / "pydantic-model-interop"

    default_check = runner.invoke(main, ["check", str(fixture)])
    import_check = runner.invoke(main, ["check", str(fixture), "--allow-python-imports"])
    default_compile = runner.invoke(main, ["compile", str(fixture), "--out", str(tmp_path / "blocked")])
    import_compile = runner.invoke(
        main,
        ["compile", str(fixture), "--out", str(tmp_path / "build"), "--allow-python-imports"],
    )

    assert default_check.exit_code == 0
    assert import_check.exit_code == 0
    assert default_compile.exit_code != 0
    assert "--allow-python-imports" in default_compile.output
    assert import_compile.exit_code == 0
    assert (tmp_path / "build" / "types" / "type-bindings.json").exists()

    default_visualize = runner.invoke(main, ["visualize", str(fixture), "--out", str(tmp_path / "viz-blocked")])
    import_visualize = runner.invoke(
        main,
        ["visualize", str(fixture), "--out", str(tmp_path / "viz"), "--allow-python-imports"],
    )
    assert default_visualize.exit_code != 0
    assert "--allow-python-imports" in default_visualize.output
    assert import_visualize.exit_code == 0
    assert (tmp_path / "viz" / "graph.json").exists()


def test_cli_monitor_reports_invalid_trace_json(tmp_path: Path) -> None:
    runner = CliRunner()
    fixture = ROOT / "examples" / "incident-command"
    trace_path = tmp_path / "bad.jsonl"
    trace_path.write_text("{bad\n")

    result = runner.invoke(main, ["monitor", str(fixture), "--trace", str(trace_path)])

    assert result.exit_code != 0
    assert "Invalid trace JSON" in result.output
    assert "bad.jsonl:1" in result.output


def test_cli_monitor_reports_invalid_contract_setup(tmp_path: Path) -> None:
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()
    (project / "bad.contract").write_text(
        """
agent BadAgent() -> MissingResult:
    goal = "bad"
""".strip()
    )
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        json.dumps(_trace_event(event_id="evt-1", event_type="agent.completed", timestamp=1.0)) + "\n"
    )

    result = runner.invoke(main, ["monitor", str(project), "--trace", str(trace_path)])

    assert result.exit_code != 0
    assert "MissingResult" in result.output


def test_cli_eval_runs_fixture_json_project(contract_project_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["eval", str(contract_project_path)])

    assert result.exit_code == 0
    assert "Fixture eval passed: 12 starts" in result.output


def _trace_event(
    *,
    run_id: str = "run-cli",
    event_id: str,
    event_type: str,
    timestamp: float,
    agent: str | None = None,
    tool: str | None = None,
    approved: bool | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {}
    if approved is not None:
        data["approved"] = approved
    payload: dict[str, object] = {
        "schema_version": "1",
        "run_id": run_id,
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "data": data,
        "provider": {},
    }
    if agent is not None:
        payload["agent"] = agent
    if tool is not None:
        payload["tool"] = tool
    return payload
