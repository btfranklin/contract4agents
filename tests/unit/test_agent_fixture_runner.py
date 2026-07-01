from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import contract4agents.fixtures as fixture_runner_module
from contract4agents.adapters.openai import OpenAITraceHooks, contract_tool_name, openai_tool_name
from contract4agents.compiler import compile_project
from contract4agents.evaluation import EvalRunner
from contract4agents.fixtures import _execution
from contract4agents.runtime import TraceRecorder
from tests.fixtures.fixture_runner import (
    DEFAULT_PROJECT,
    FixtureArtifactError,
    FixtureConfigError,
    FixtureReport,
    StartReport,
    load_fixture_metadata,
    run_fixture_project_sync,
    verify_fixture_artifacts,
)


def test_fixture_metadata_loads_and_rejects_missing(tmp_path: Path) -> None:
    metadata = load_fixture_metadata(DEFAULT_PROJECT)
    assert metadata["entry_agent"] == "OpsDeskCoordinator"

    with pytest.raises(FixtureConfigError):
        load_fixture_metadata(tmp_path)


def test_fixture_artifact_verifier_passes_and_fails(tmp_path: Path) -> None:
    metadata = load_fixture_metadata(DEFAULT_PROJECT)
    artifacts = compile_project(DEFAULT_PROJECT, tmp_path / "build")

    checks = verify_fixture_artifacts(metadata, artifacts, tmp_path / "build")

    assert "expected agents present" in checks
    bad_metadata = dict(metadata)
    bad_metadata["expected"] = {**metadata["expected"], "eval_count": 999}
    with pytest.raises(FixtureArtifactError):
        verify_fixture_artifacts(bad_metadata, artifacts, tmp_path / "build")


def test_fixture_report_serializer_includes_required_sections() -> None:
    report = FixtureReport(
        project="project",
        mode="local",
        artifact_checks=["compiled"],
        starts=[
            StartReport(
                start_id="case",
                passed=True,
                skipped_semantic=['semantic(output, "ok")'],
                monitor_violations=[],
            )
        ],
        cleaned=True,
        run_root="/tmp/run",
    )

    data = report.to_dict()

    assert data["summary"]["passed"] is True
    assert data["artifact_checks"] == ["compiled"]
    assert data["starts"][0]["skipped_semantic"]
    assert data["cleaned"] is True


def test_fixture_runner_preserves_artifacts_on_failure(tmp_path: Path) -> None:
    run_root = tmp_path / "failed-run"

    with pytest.raises(FixtureConfigError):
        run_fixture_project_sync(project_root=DEFAULT_PROJECT, run_root=run_root, mode="unknown")

    assert (run_root / "reports" / "report.json").exists()
    assert "# Contract4Agents Fixture Report" in (run_root / "reports" / "report.md").read_text()
    assert (run_root / "build").exists()
    assert (run_root / "data").exists()


def test_fixture_runner_reports_active_start_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_root = tmp_path / "failed-start"

    async def failing_runner(
        start: Any,
        _db_path: Path,
        _artifacts: dict[str, Any],
        _trace_path: Path,
    ) -> tuple[dict[str, Any], TraceRecorder]:
        raise RuntimeError(f"boom {start.start_id}")

    monkeypatch.setattr(_execution, "runner_for_mode", lambda _metadata, _mode: failing_runner)

    with pytest.raises(RuntimeError):
        fixture_runner_module.run_fixture_project_sync(project_root=DEFAULT_PROJECT, run_root=run_root, mode="local")

    report = json.loads((run_root / "reports" / "report.json").read_text())
    assert report["starts"][0]["start_id"] == "billing_invoice_explain"
    assert "boom billing_invoice_explain" in report["starts"][0]["failures"][0]
    assert (run_root / "traces").exists()


def test_fixture_runner_reports_assertion_failures_separately(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(DEFAULT_PROJECT, project)
    coordinator = project / "agents" / "ops_desk_coordinator.contract"
    coordinator.write_text(
        coordinator.read_text().replace(
            "expect(output.reply excludes hidden_truth)",
            "expect(output.reply contains definitely_missing_assertion_token)",
        )
    )

    report = fixture_runner_module.run_fixture_project_sync(project_root=project, run_root=tmp_path / "run")

    assert not report.passed
    assert any(item.assertion_failures for item in report.starts)
    failed = next(item for item in report.starts if item.assertion_failures)
    assert failed.failures == []
    assert "definitely_missing_assertion_token" in failed.assertion_failures[0]


@pytest.mark.asyncio
async def test_eval_runner_supports_generic_trace_and_hidden_truth_checks() -> None:
    trace = TraceRecorder()
    trace.record("approval.completed", tool="billing.create_credit", approved=True)
    trace.record("approval.completed", tool="access.grant_access", approved=False)
    trace.record("guardrail.rejected", guardrail="prompt_injection")
    runner = EvalRunner(
        {
            "Result": {
                "type": "object",
                "properties": {"reply": {"type": "string"}},
                "required": ["reply"],
            }
        }
    )

    result = await runner.evaluate(
        name="case",
        output={"reply": "duplicate invoice credit approval"},
        output_type="Result",
        trace=trace,
        expectations=[
            "output discovers hidden_truth.expected_resolution",
            "trace.approval_granted(billing.create_credit)",
            "trace.approval_denied(access.grant_access)",
            "trace.guardrail_rejected(prompt_injection)",
        ],
        hidden_truth={"expected_resolution": "duplicate invoice credit"},
    )

    assert result.passed


@pytest.mark.asyncio
async def test_openai_trace_hooks_and_tool_name_helpers() -> None:
    trace = TraceRecorder()
    hooks = OpenAITraceHooks(trace)

    assert openai_tool_name("billing.create_credit") == "billing__create_credit"
    assert contract_tool_name("billing__create_credit") == "billing.create_credit"

    await hooks.on_agent_start(None, SimpleNamespace(name="AgentA"))
    await hooks.on_tool_start(None, SimpleNamespace(name="AgentA"), SimpleNamespace(name="billing__create_credit"))
    await hooks.on_tool_end(None, SimpleNamespace(name="AgentA"), SimpleNamespace(name="billing__create_credit"), "ok")
    await hooks.on_agent_end(None, SimpleNamespace(name="AgentA"), {"ok": True})

    assert trace.count("agent.started", "AgentA") == 1
    assert trace.count("tool.started", "billing.create_credit") == 1
    assert trace.count("tool.completed", "billing.create_credit") == 1
