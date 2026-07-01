from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contract4agents.assertions import evaluate_agent_assertions, evaluate_run_contract
from contract4agents.compiler import AgentManifest, CompilerArtifacts
from contract4agents.runtime import TraceRecorder, load_trace_jsonl


def test_evaluate_agent_assertions_passes_output_trace_and_hidden_truth() -> None:
    trace = TraceRecorder()
    trace.record("tool.completed", tool="tools.lookup")
    manifest = _manifest(
        [
            "expect(output conforms Result)",
            "expect(output.ok == true)",
            "expect(trace.tool_called(tools.lookup))",
            "expect(output discovers hidden_truth.discovery)",
        ]
    )

    result = evaluate_agent_assertions(
        manifest=manifest,
        output={"ok": True, "summary": "alpha discovered"},
        trace=trace,
        schemas=_schemas(),
        hidden_truth={"discovery": "alpha discovered"},
    )

    assert result.passed
    assert [check.status for check in result.checks] == ["passed", "passed", "passed", "passed"]


def test_evaluate_agent_assertions_reports_output_trace_and_unsupported_failures() -> None:
    manifest = _manifest(
        [
            "expect(output.ok == true)",
            "expect(trace.tool_called(tools.lookup))",
            "expect(trace.toool_called(tools.lookup))",
        ]
    )

    result = evaluate_agent_assertions(
        manifest=manifest,
        output={"ok": False, "summary": "nope"},
        trace=TraceRecorder(),
        schemas=_schemas(),
    )

    assert not result.passed
    assert [check.failure.kind for check in result.checks if check.failure] == ["output", "trace", "unsupported"]


def test_evaluate_agent_assertions_skips_false_condition_and_checks_true_condition() -> None:
    manifest = _manifest(
        [
            "when(trace.tool_called(tools.missing), expect(output.ok == false))",
            "when (trace.tool_called(tools.lookup), expect(output.ok == true))",
        ]
    )
    trace = TraceRecorder()
    trace.record("tool.completed", tool="tools.lookup")

    result = evaluate_agent_assertions(
        manifest=manifest,
        output={"ok": True, "summary": "ok"},
        trace=trace,
        schemas=_schemas(),
    )

    assert result.passed
    assert [check.status for check in result.checks] == ["skipped", "passed"]


def test_evaluate_agent_assertions_fails_true_condition_expectation() -> None:
    manifest = _manifest(["when(trace.tool_called(tools.lookup), expect(output.ok == true))"])
    trace = TraceRecorder()
    trace.record("tool.completed", tool="tools.lookup")

    result = evaluate_agent_assertions(
        manifest=manifest,
        output={"ok": False, "summary": "bad"},
        trace=trace,
        schemas=_schemas(),
    )

    assert not result.passed
    assert result.checks[0].failure
    assert result.checks[0].failure.kind == "output"


def test_evaluate_run_contract_reports_missing_target_output_and_manifest() -> None:
    contract = _artifacts(_manifest(["expect(output.ok == true)"]))

    result = evaluate_run_contract(
        contract=contract,
        trace=TraceRecorder(),
        outputs={},
        target_agents=["ExampleAgent", "MissingAgent"],
    )

    assert not result.passed
    assert [failure.kind for failure in result.failures] == ["missing_output", "contract"]


def test_evaluate_run_contract_defaults_to_outputs_present() -> None:
    contract = _artifacts(_manifest(["expect(output.ok == true)"]))

    result = evaluate_run_contract(
        contract=contract,
        trace=TraceRecorder(),
        outputs={"ExampleAgent": {"ok": True, "summary": "ok"}},
    )

    assert result.passed


def test_evaluate_run_contract_accepts_loaded_canonical_trace(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-assertions",
                "event_id": "evt-1",
                "event_type": "tool.completed",
                "timestamp": 1.0,
                "tool": "tools.lookup",
                "data": {},
                "provider": {},
            }
        )
        + "\n"
    )
    contract = _artifacts(_manifest(["expect(trace.tool_called(tools.lookup))"]))

    result = evaluate_run_contract(
        contract=contract,
        trace=load_trace_jsonl(path),
        outputs={"ExampleAgent": {"ok": True, "summary": "ok"}},
    )

    assert result.passed


def _manifest(assertions: list[str]) -> AgentManifest:
    return {
        "agent": "ExampleAgent",
        "description": "",
        "goal": "",
        "inputs": [],
        "output": {"type": "Result", "schema_ref": "schemas/Result.json"},
        "tools": [{"name": "tools.lookup", "module": "tools", "permission": "available"}],
        "agents": [],
        "datasources": [],
        "policy": [],
        "success": [],
        "routes": [],
        "composition": [],
        "guards": [],
        "assertions": assertions,
    }


def _schemas() -> dict[str, dict[str, Any]]:
    return {
        "Result": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}, "summary": {"type": "string"}},
            "required": ["ok", "summary"],
        }
    }


def _artifacts(manifest: AgentManifest) -> CompilerArtifacts:
    return {
        "schemas": _schemas(),
        "manifests": {"ExampleAgent": manifest},
        "instructions": {"ExampleAgent": "instructions"},
        "evals": [],
        "monitors": [],
        "guard_plan": [],
        "adapter_capability_matrix": {},
        "docs": {},
    }
