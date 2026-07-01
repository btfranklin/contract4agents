from __future__ import annotations

import json
import shutil
import sqlite3
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

ROOT = Path(__file__).resolve().parents[2]
INCIDENT_PROJECT = ROOT / "examples" / "incident-command"


def test_fixture_metadata_loads_and_rejects_missing(tmp_path: Path) -> None:
    metadata = load_fixture_metadata(DEFAULT_PROJECT)
    assert metadata["entry_agent"] == "OpsDeskCoordinator"

    with pytest.raises(FixtureConfigError):
        load_fixture_metadata(tmp_path)


def test_local_fixture_metadata_does_not_require_live_runner() -> None:
    metadata = load_fixture_metadata(INCIDENT_PROJECT)

    assert metadata["entry_agent"] == "IncidentCommander"
    assert "live_runner" not in metadata
    assert _execution.runner_for_mode(metadata, "local")
    with pytest.raises(FixtureConfigError, match="requires `live_runner`"):
        _execution.runner_for_mode(metadata, "openai")


def test_fixture_artifact_verifier_passes_and_fails(tmp_path: Path) -> None:
    metadata = load_fixture_metadata(DEFAULT_PROJECT)
    artifacts = compile_project(DEFAULT_PROJECT, tmp_path / "build")

    checks = verify_fixture_artifacts(metadata, artifacts, tmp_path / "build")

    assert "expected agents present" in checks
    bad_metadata = dict(metadata)
    bad_metadata["expected"] = {**metadata["expected"], "eval_count": 999}
    with pytest.raises(FixtureArtifactError):
        verify_fixture_artifacts(bad_metadata, artifacts, tmp_path / "build")


def test_incident_command_fixture_artifact_verifier_passes(tmp_path: Path) -> None:
    metadata = load_fixture_metadata(INCIDENT_PROJECT)
    artifacts = compile_project(INCIDENT_PROJECT, tmp_path / "build")

    checks = verify_fixture_artifacts(metadata, artifacts, tmp_path / "build")

    assert "expected generated files present" in checks
    assert "expected tools and permissions present" in checks


def test_fixture_artifact_verifier_uses_declared_tool_permissions(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    for relative in [
        "schemas/ResearchResult.json",
        "manifests/ResearchCoordinator.json",
        "instructions/ResearchCoordinator.md",
        "evals/evals.json",
        "monitors/monitors.json",
        "adapters/capability-matrix.json",
        "docs/summary.md",
    ]:
        path = build_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")

    metadata = {
        "entry_agent": "ResearchCoordinator",
        "output_type": "ResearchResult",
        "expected": {
            "agents": ["ResearchCoordinator"],
            "types": ["ResearchResult"],
            "tools": ["research.publish_brief", "research.search"],
            "tool_permissions": {"research.publish_brief": "requires_approval"},
            "datasources": ["ResearchSource"],
            "eval_count": 1,
            "monitor_count": 1,
        },
    }
    artifacts = {
        "schemas": {"ResearchResult": {"type": "object"}},
        "type_bindings": [],
        "manifests": {
            "ResearchCoordinator": {
                "agent": "ResearchCoordinator",
                "source_path": "agents/research.contract",
                "description": "",
                "goal": "",
                "inputs": [],
                "output": {
                    "type": "ResearchResult",
                    "schema_ref": "schemas/ResearchResult.json",
                    "python_ref": None,
                },
                "tools": [
                    {
                        "name": "research.publish_brief",
                        "module": "tools.research",
                        "permission": "requires_approval",
                    },
                    {"name": "research.search", "module": "tools.research", "permission": "preapproved"},
                ],
                "hosted_tools": [],
                "agents": [],
                "datasources": [
                    {
                        "name": "ResearchSource",
                        "python": "tests.fixtures.research:source",
                        "produces": "ResearchInput",
                        "requires": [],
                        "render": "markdown",
                        "cache": "none",
                    }
                ],
                "policy": [],
                "success": [],
                "routes": [],
                "composition": [],
                "guards": [],
                "assertions": [],
            }
        },
        "instructions": {},
        "evals": [
            {
                "name": "case",
                "agent": "ResearchCoordinator",
                "givens": {},
                "expects": [],
                "semantic_expects": [],
            }
        ],
        "monitors": [
            {
                "name": "publish_requires_approval",
                "agent": "ResearchCoordinator",
                "severity": "error",
                "when": "trace.tool_called(research.publish_brief)",
                "expect": "trace.approval_granted(research.publish_brief)",
            }
        ],
        "guard_plan": [],
        "adapter_capability_matrix": {
            "openai": {
                "trace_capture": {
                    "status": "partial",
                    "caveats": [],
                }
            }
        },
        "docs": {},
    }

    checks = verify_fixture_artifacts(metadata, artifacts, build_dir)

    assert "expected tools and permissions present" in checks
    bad_metadata = dict(metadata)
    bad_metadata["expected"] = {
        **metadata["expected"],
        "tool_permissions": {"research.publish_brief": "preapproved"},
    }
    with pytest.raises(FixtureArtifactError, match="research.publish_brief"):
        verify_fixture_artifacts(bad_metadata, artifacts, build_dir)


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


@pytest.mark.asyncio
async def test_fixture_retry_uses_attempt_local_db_and_trace(tmp_path: Path) -> None:
    base_db = tmp_path / "base.sqlite"
    with sqlite3.connect(base_db) as db:
        db.execute("create table markers (value text)")
    seen: list[tuple[Path, Path]] = []

    async def flaky_runner(
        _start: Any,
        db_path: Path,
        _artifacts: dict[str, Any],
        trace_path: Path,
    ) -> tuple[dict[str, Any], TraceRecorder]:
        seen.append((db_path, trace_path))
        trace = TraceRecorder(trace_path, run_id=f"run-attempt-{len(seen)}")
        trace.record("tool.completed", event_id=f"evt-{len(seen)}", timestamp=float(len(seen)), tool="fixture.tool")
        with sqlite3.connect(db_path) as db:
            marker_count = db.execute("select count(*) from markers").fetchone()[0]
            if len(seen) == 1:
                db.execute("insert into markers values ('failed-attempt')")
                db.commit()
                raise RuntimeError("transient fixture failure")
        return {"marker_count": marker_count}, trace

    output, trace, attempts, retry_errors, attempt_db_path = await _execution.run_start_with_retry(
        flaky_runner,
        SimpleNamespace(start_id="case"),
        base_db,
        {},
        tmp_path / "traces" / "case.jsonl",
        "openai",
    )

    assert output == {"marker_count": 0}
    assert attempts == 2
    assert retry_errors == ["RuntimeError: transient fixture failure"]
    assert attempt_db_path == seen[1][0]
    assert seen[0][0] != seen[1][0]
    assert seen[0][1] != seen[1][1]
    assert trace.run_id == "run-attempt-2"
    assert "failed-attempt" in _db_markers(seen[0][0])
    assert _db_markers(seen[1][0]) == []
    assert "evt-1" in seen[0][1].read_text()
    assert "evt-2" in seen[1][1].read_text()


def _db_markers(path: Path) -> list[str]:
    with sqlite3.connect(path) as db:
        return [row[0] for row in db.execute("select value from markers").fetchall()]


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

    sdk_tool_name = openai_tool_name("billing.create_credit")
    assert contract_tool_name(sdk_tool_name) == "billing.create_credit"

    await hooks.on_agent_start(None, SimpleNamespace(name="AgentA"))
    await hooks.on_tool_start(None, SimpleNamespace(name="AgentA"), SimpleNamespace(name=sdk_tool_name))
    await hooks.on_tool_end(None, SimpleNamespace(name="AgentA"), SimpleNamespace(name=sdk_tool_name), "ok")
    await hooks.on_agent_end(None, SimpleNamespace(name="AgentA"), {"ok": True})

    assert trace.count("agent.started", "AgentA") == 1
    assert trace.count("tool.started", "billing.create_credit") == 1
    assert trace.count("tool.completed", "billing.create_credit") == 1
