from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from contract4agents.assertions import evaluate_agent_assertions, evaluate_run_assertions, evaluate_run_spec
from contract4agents.compiler import AgentManifest, CompilerArtifacts
from contract4agents.runtime import TraceRecorder, TraceScopeError, load_trace_jsonl


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


def test_evaluate_run_assertions_reports_missing_target_output_and_manifest() -> None:
    contract = _artifacts(_manifest(["expect(output.ok == true)"]))

    result = evaluate_run_assertions(
        contract=contract,
        trace=TraceRecorder(),
        outputs={},
        target_agents=["ExampleAgent", "MissingAgent"],
    )

    assert not result.passed
    assert [failure.kind for failure in result.failures] == ["missing_output", "contract"]


def test_evaluate_run_assertions_defaults_to_outputs_present() -> None:
    contract = _artifacts(_manifest(["expect(output.ok == true)"]))

    result = evaluate_run_assertions(
        contract=contract,
        trace=TraceRecorder(),
        outputs={"ExampleAgent": {"ok": True, "summary": "ok"}},
    )

    assert result.passed


def test_evaluate_run_assertions_accepts_loaded_canonical_trace(tmp_path: Path) -> None:
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

    result = evaluate_run_assertions(
        contract=contract,
        trace=load_trace_jsonl(path),
        outputs={"ExampleAgent": {"ok": True, "summary": "ok"}},
    )

    assert result.passed


def test_evaluate_run_assertions_accepts_hosted_tool_trace(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-assertions",
                "event_id": "evt-1",
                "event_type": "hosted_tool.completed",
                "timestamp": 1.0,
                "tool": "openai.web_search",
                "data": {},
                "provider": {},
            }
        )
        + "\n"
    )
    manifest = _manifest(["expect(trace.hosted_tool_called(openai.web_search))"])
    contract = _artifacts(manifest)

    result = evaluate_run_assertions(
        contract=contract,
        trace=load_trace_jsonl(path),
        outputs={"ExampleAgent": {"ok": True, "summary": "ok"}},
    )

    assert result.passed


def test_evaluate_run_assertions_requires_run_id_for_multi_run_trace() -> None:
    trace = TraceRecorder()
    trace.record("tool.completed", run_id="run-a", tool="tools.lookup")
    trace.record("tool.completed", run_id="run-b", tool="tools.other")
    contract = _artifacts(_manifest(["expect(trace.tool_called(tools.lookup))"]))

    with pytest.raises(TraceScopeError):
        evaluate_run_assertions(
            contract=contract,
            trace=trace,
            outputs={"ExampleAgent": {"ok": True, "summary": "ok"}},
        )

    scoped_result = evaluate_run_assertions(
        contract=contract,
        trace=trace,
        outputs={"ExampleAgent": {"ok": True, "summary": "ok"}},
        run_id="run-b",
    )

    assert not scoped_result.passed
    assert scoped_result.failures[0].kind == "trace"


def test_evaluate_run_spec_passes_stage_outputs_and_trace() -> None:
    trace = TraceRecorder()
    trace.record("agent.completed", agent="ExampleAgent")
    trace.record("agent.completed", agent="WriterAgent")
    contract = _artifacts(
        _manifest([]),
        extra_manifests=[_manifest([], agent="WriterAgent")],
        run_specs=[_run_spec_artifact()],
    )

    result = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=trace,
        stage_outputs={
            "plan": {"ok": True, "summary": "plan"},
            "sections": [{"ok": True, "summary": "section"}],
            "synthesis": {"ok": True, "summary": "final"},
        },
    )

    assert result.passed
    assert [(item.stage, item.status) for item in result.stages] == [
        ("plan", "passed"),
        ("sections", "passed"),
        ("review", "skipped"),
        ("synthesis", "passed"),
    ]
    assert [check.status for check in result.assertions] == ["passed", "passed", "passed"]


def test_evaluate_run_spec_reports_missing_stage_output() -> None:
    contract = _artifacts(
        _manifest([]),
        extra_manifests=[_manifest([], agent="WriterAgent")],
        run_specs=[_run_spec_artifact()],
    )

    result = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=TraceRecorder(),
        stage_outputs={
            "plan": {"ok": True, "summary": "plan"},
            "sections": [{"ok": True, "summary": "section"}],
        },
    )

    assert not result.passed
    assert result.failures[0].kind == "missing_stage_output"


def test_evaluate_run_spec_reports_schema_and_cardinality_failures() -> None:
    contract = _artifacts(_manifest([]), run_specs=[_run_spec_artifact()])

    result = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=TraceRecorder(),
        stage_outputs={
            "plan": {"ok": "yes", "summary": "plan"},
            "sections": [],
            "synthesis": {"ok": True, "summary": "final"},
        },
    )

    assert not result.passed
    assert {failure.kind for failure in result.failures} >= {"stage_schema", "malformed_stage_output"}


def test_evaluate_run_spec_checks_not_tool_called_by() -> None:
    trace = TraceRecorder()
    trace.record("agent.completed", agent="ExampleAgent")
    trace.record("agent.completed", agent="WriterAgent")
    trace.record("hosted_tool.completed", agent="WriterAgent", tool="openai.web_search")
    contract = _artifacts(
        _manifest([]),
        extra_manifests=[_manifest([], agent="WriterAgent")],
        run_specs=[_run_spec_artifact()],
    )

    result = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=trace,
        stage_outputs={
            "plan": {"ok": True, "summary": "plan"},
            "sections": [{"ok": True, "summary": "section"}],
            "synthesis": {"ok": True, "summary": "final"},
        },
    )

    assert not result.passed
    assert result.failures[-1].kind == "trace"


def test_evaluate_run_spec_supports_stage_name_trace_spies() -> None:
    trace = TraceRecorder()
    trace.record("stage.completed", stage="plan")
    trace.record("stage.completed", stage="synthesis")
    run_spec = {**_run_spec_artifact(), "assertions": ["expect(trace.called(plan))"]}
    contract = _artifacts(
        _manifest([]),
        extra_manifests=[_manifest([], agent="WriterAgent")],
        run_specs=[run_spec],
    )

    result = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=trace,
        stage_outputs={
            "plan": {"ok": True, "summary": "plan"},
            "sections": [{"ok": True, "summary": "section"}],
            "synthesis": {"ok": True, "summary": "final"},
        },
    )

    assert result.passed
    assert result.assertions[0].status == "passed"


def test_evaluate_run_spec_evaluates_structured_conditionals() -> None:
    trace = TraceRecorder()
    trace.record("stage.completed", stage="plan")
    trace.record("stage.completed", stage="synthesis")
    run_spec = {
        **_run_spec_artifact(),
        "assertions": [
            "when(trace.called(plan), expect(trace.called(synthesis)))",
            "when(trace.called(review), expect(trace.called(missing)))",
        ],
    }
    contract = _artifacts(
        _manifest([]),
        extra_manifests=[_manifest([], agent="WriterAgent")],
        run_specs=[run_spec],
    )

    result = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=trace,
        stage_outputs={
            "plan": {"ok": True, "summary": "plan"},
            "sections": [{"ok": True, "summary": "section"}],
            "synthesis": {"ok": True, "summary": "final"},
        },
    )

    assert result.passed
    assert [check.status for check in result.assertions] == ["passed", "skipped"]


def test_evaluate_run_spec_passes_derived_value_subset_assertion() -> None:
    run_spec = {
        **_run_spec_artifact(),
        "assertions": ["expect(value.synthesis_citation_ids subset_of value.ledger_cited_ids)"],
    }
    contract = _artifacts(_manifest([]), run_specs=[run_spec])

    result = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=TraceRecorder(),
        stage_outputs=_valid_stage_outputs(),
        derived_values={
            "synthesis_citation_ids": ["C01", "C02"],
            "ledger_cited_ids": ["C01", "C02", "C03"],
        },
    )

    assert result.passed
    assert result.assertions[0].status == "passed"


def test_evaluate_run_spec_reports_missing_subset_values() -> None:
    run_spec = {
        **_run_spec_artifact(),
        "assertions": ["expect(value.synthesis_citation_ids subset_of value.ledger_cited_ids)"],
    }
    contract = _artifacts(_manifest([]), run_specs=[run_spec])

    result = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=TraceRecorder(),
        stage_outputs=_valid_stage_outputs(),
        derived_values={
            "synthesis_citation_ids": ["C01", "C04", "C07"],
            "ledger_cited_ids": ["C01", "C02", "C03"],
        },
    )

    assert not result.passed
    assert result.failures[0].kind == "data_relation"
    assert "missing C04, C07" in result.failures[0].message


@pytest.mark.parametrize(
    ("assertion", "derived_values", "passed"),
    [
        (
            "expect(value.ledger_cited_ids contains_all value.synthesis_citation_ids)",
            {"ledger_cited_ids": ["A", "B"], "synthesis_citation_ids": ["A"]},
            True,
        ),
        ("expect(value.left equals_set value.right)", {"left": ["B", "A"], "right": ["A", "B"]}, True),
        ("expect(value.left intersects value.right)", {"left": ["A", "B"], "right": ["C", "B"]}, True),
        ("expect(value.left disjoint_from value.right)", {"left": ["A", "B"], "right": ["C", "D"]}, True),
        ("expect(value.left intersects value.right)", {"left": ["A"], "right": ["B"]}, False),
        ("expect(value.left disjoint_from value.right)", {"left": ["A"], "right": ["A", "B"]}, False),
    ],
)
def test_evaluate_run_spec_checks_derived_value_set_operators(
    assertion: str,
    derived_values: dict[str, list[str]],
    passed: bool,
) -> None:
    run_spec = {**_run_spec_artifact(), "assertions": [assertion]}
    contract = _artifacts(_manifest([]), run_specs=[run_spec])

    result = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=TraceRecorder(),
        stage_outputs=_valid_stage_outputs(),
        derived_values=derived_values,
    )

    assert result.passed is passed


def test_evaluate_run_spec_data_relation_failures_are_closed() -> None:
    run_spec = {
        **_run_spec_artifact(),
        "assertions": [
            "expect(value.left subset_of value.right)",
            "expect(value.left overlaps value.right)",
        ],
    }
    contract = _artifacts(_manifest([]), run_specs=[run_spec])

    missing_values = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=TraceRecorder(),
        stage_outputs=_valid_stage_outputs(),
    )
    unknown_value = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=TraceRecorder(),
        stage_outputs=_valid_stage_outputs(),
        derived_values={"left": ["A"]},
    )
    non_scalar = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=TraceRecorder(),
        stage_outputs=_valid_stage_outputs(),
        derived_values={"left": [{"id": "A"}], "right": ["A"]},
    )

    assert not missing_values.passed
    assert missing_values.failures[0].kind == "data_relation"
    assert "No derived values supplied" in missing_values.failures[0].message
    assert not unknown_value.passed
    assert "Unknown derived value `value.right`" in unknown_value.failures[0].message
    assert not non_scalar.passed
    assert "contains non-scalar item" in non_scalar.failures[0].message
    assert missing_values.assertions[1].failure
    assert missing_values.assertions[1].failure.kind == "unsupported"


def test_evaluate_run_spec_supports_conditional_derived_value_assertion() -> None:
    trace = TraceRecorder()
    trace.record("stage.completed", stage="synthesis")
    run_spec = {
        **_run_spec_artifact(),
        "assertions": ["when(trace.called(synthesis), expect(value.left subset_of value.right))"],
    }
    contract = _artifacts(_manifest([]), run_specs=[run_spec])

    result = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=trace,
        stage_outputs=_valid_stage_outputs(),
        derived_values={"left": ["A"], "right": ["A", "B"]},
    )

    assert result.passed


def test_evaluate_run_spec_scopes_multi_run_trace_by_run_id() -> None:
    trace = TraceRecorder()
    trace.record("stage.completed", run_id="run-a", stage="plan")
    trace.record("stage.completed", run_id="run-b", stage="synthesis")
    run_spec = {**_run_spec_artifact(), "assertions": ["expect(trace.called(plan))"]}
    contract = _artifacts(
        _manifest([]),
        extra_manifests=[_manifest([], agent="WriterAgent")],
        run_specs=[run_spec],
    )
    stage_outputs = {
        "plan": {"ok": True, "summary": "plan"},
        "sections": [{"ok": True, "summary": "section"}],
        "synthesis": {"ok": True, "summary": "final"},
    }

    with pytest.raises(TraceScopeError):
        evaluate_run_spec(contract=contract, run_spec="ExampleRun", trace=trace, stage_outputs=stage_outputs)

    scoped = evaluate_run_spec(
        contract=contract,
        run_spec="ExampleRun",
        trace=trace,
        stage_outputs=stage_outputs,
        run_id="run-b",
    )

    assert not scoped.passed
    assert scoped.failures[0].kind == "trace"


def test_evaluate_agent_assertions_ignore_other_agent_trace_events() -> None:
    trace = TraceRecorder()
    trace.record("tool.completed", agent="OtherAgent", tool="tools.lookup")

    result = evaluate_agent_assertions(
        manifest=_manifest(["expect(trace.tool_called(tools.lookup))"]),
        output={"ok": True, "summary": "ok"},
        trace=trace,
        schemas=_schemas(),
    )

    assert not result.passed
    assert result.checks[0].failure
    assert result.checks[0].failure.kind == "trace"


def _valid_stage_outputs() -> dict[str, Any]:
    return {
        "plan": {"ok": True, "summary": "plan"},
        "sections": [{"ok": True, "summary": "section"}],
        "synthesis": {"ok": True, "summary": "final"},
    }


def _manifest(assertions: list[str], agent: str = "ExampleAgent") -> AgentManifest:
    return {
        "agent": agent,
        "source_path": "agents/example.contract",
        "description": "",
        "goal": "",
        "inputs": [],
        "output": {"type": "Result", "schema_ref": "schemas/Result.json", "python_ref": None},
        "tools": [{"name": "tools.lookup", "module": "tools", "permission": "available"}],
        "hosted_tools": [
            {
                "name": "openai.web_search",
                "provider": "openai",
                "tool": "web_search",
                "config": {"context_size": "medium"},
                "permission": "available",
            }
        ],
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


def _artifacts(
    manifest: AgentManifest,
    *,
    extra_manifests: list[AgentManifest] | None = None,
    run_specs: list[dict[str, Any]] | None = None,
) -> CompilerArtifacts:
    manifests = {manifest["agent"]: manifest}
    for extra_manifest in extra_manifests or []:
        manifests[extra_manifest["agent"]] = extra_manifest
    return {
        "schemas": _schemas(),
        "type_bindings": [
            {
                "type": "Result",
                "source": "native",
                "python_ref": None,
                "schema_ref": "schemas/Result.json",
                "schema_hash": "test",
            }
        ],
        "manifests": manifests,
        "instructions": {"ExampleAgent": "instructions"},
        "evals": [],
        "monitors": [],
        "run_specs": run_specs or [],
        "guard_plan": [],
        "adapter_capability_matrix": {},
        "docs": {},
    }


def _run_spec_artifact() -> dict[str, Any]:
    return {
        "name": "ExampleRun",
        "source_path": "runs/example.contract",
        "stages": [
            {
                "name": "plan",
                "agent": "ExampleAgent",
                "output_type": "Result",
                "cardinality": "one",
                "manifest_ref": "manifests/ExampleAgent.json",
                "schema_ref": "schemas/Result.json",
            },
            {
                "name": "sections",
                "agent": "ExampleAgent",
                "output_type": "Result",
                "cardinality": "many",
                "manifest_ref": "manifests/ExampleAgent.json",
                "schema_ref": "schemas/Result.json",
            },
            {
                "name": "review",
                "agent": "WriterAgent",
                "output_type": "Result",
                "cardinality": "optional",
                "manifest_ref": "manifests/WriterAgent.json",
                "schema_ref": "schemas/Result.json",
            },
            {
                "name": "synthesis",
                "agent": "WriterAgent",
                "output_type": "Result",
                "cardinality": "one",
                "manifest_ref": "manifests/WriterAgent.json",
                "schema_ref": "schemas/Result.json",
            },
        ],
        "assertions": [
            "expect(trace.called_before(ExampleAgent, WriterAgent))",
            "expect(trace.max_calls(WriterAgent, 2))",
            "expect(trace.not_tool_called_by(WriterAgent, openai.web_search))",
        ],
    }
