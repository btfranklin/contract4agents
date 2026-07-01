from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract4agents.compiler import compile_project
from contract4agents.evaluation import EvalRunner
from contract4agents.expressions import (
    ExpressionError,
    parse_contract_expression,
    parse_expectation,
    parse_monitor_condition,
    parse_semantic_expectation,
)
from contract4agents.expressions._trace_ops import TRACE_OPS
from contract4agents.monitor import MonitorRule, run_monitors
from contract4agents.runtime import (
    AmbiguousDatasource,
    ContextValue,
    DatasourceContext,
    DatasourceRegistry,
    DatasourceSpec,
    FakeToolRegistry,
    MissingContextSlot,
    RuntimeContext,
    ToolPermissionDenied,
    TraceRecorder,
    datasource,
)
from examples.incident_command_imports.harness import (
    load_hidden_truth,
    run_incident_command_harness,
    seed_incident_command,
)


@pytest.mark.asyncio
async def test_runtime_resolves_datasource() -> None:
    def render(value: object) -> str:
        return f"value={value}"

    async def resolve(_ctx: DatasourceContext) -> str:
        return "resolved"

    registry = DatasourceRegistry()
    registry.register("Example", DatasourceSpec("Example", "ExampleType", [], resolve, render))
    runtime = RuntimeContext()

    value = await runtime.resolve_one("ExampleType", registry)

    assert value.type_name == "ExampleType"
    assert value.rendered == "value=resolved"
    assert runtime.trace.count("datasource.resolved") == 1


@pytest.mark.asyncio
async def test_runtime_detects_missing_and_ambiguous_datasources() -> None:
    runtime = RuntimeContext()
    with pytest.raises(MissingContextSlot):
        await runtime.resolve_one("Missing", DatasourceRegistry())

    registry = DatasourceRegistry()
    registry.register("A", DatasourceSpec("A", "Thing", [], lambda _ctx: ContextValue("Thing", 1, "1", "A")))
    registry.register("B", DatasourceSpec("B", "Thing", [], lambda _ctx: ContextValue("Thing", 2, "2", "B")))
    with pytest.raises(AmbiguousDatasource):
        await runtime.resolve_one("Thing", registry)


def test_datasource_decorator_registration() -> None:
    @datasource(produces="Decorated", requires=[], render=lambda value: str(value))
    def resolve(_ctx: DatasourceContext) -> int:
        return 42

    registry = DatasourceRegistry()
    registry.register_func("Decorated", resolve)

    assert registry.by_output("Decorated")[0].name == "Decorated"


@pytest.mark.asyncio
async def test_fake_tool_registry_approval_paths() -> None:
    trace = TraceRecorder()
    registry = FakeToolRegistry(approval_callback=lambda _name, _kwargs: False)
    registry.register("danger", lambda: {"ok": True}, "requires_approval")

    with pytest.raises(ToolPermissionDenied):
        await registry.call("danger", trace)

    assert trace.count("approval.completed") == 1
    assert trace.count("tool.denied") == 1


def test_trace_recorder_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    trace = TraceRecorder(path, run_id="run-r1")
    trace.record("agent.started", event_id="evt-r1", timestamp=1.0, agent="IncidentCommander")

    payload = json.loads(path.read_text())
    assert payload["schema_version"] == "1"
    assert payload["run_id"] == "run-r1"
    assert payload["event_id"] == "evt-r1"
    assert payload["event_type"] == "agent.started"
    assert payload["agent"] == "IncidentCommander"
    assert payload["data"] == {}


@pytest.mark.asyncio
async def test_eval_runner_checks_output_trace_and_hidden_truth() -> None:
    schema = {
        "IncidentBrief": {
            "type": "object",
            "properties": {"likely_cause": {"type": "string"}, "summary": {"type": "string"}},
            "required": ["likely_cause", "summary"],
        }
    }
    trace = TraceRecorder()
    trace.record("tool.completed", tool="logs.search")
    runner = EvalRunner(schema)

    result = await runner.evaluate(
        name="case",
        output={"summary": "checkout", "likely_cause": "deploy 8f31c2 changed timeout handling"},
        output_type="IncidentBrief",
        trace=trace,
        expectations=[
            "output conforms IncidentBrief",
            "output.likely_cause contains 8f31c2",
            "trace.tool_called(logs.search)",
            "output discovers hidden_truth.likely_cause",
        ],
        semantic_expectations=['semantic(output, "good")'],
        hidden_truth={"likely_cause": "deploy 8f31c2 changed timeout handling"},
    )

    assert result.passed
    assert result.skipped_semantic


@pytest.mark.asyncio
async def test_eval_runner_validates_nested_native_output_schema(tmp_path: Path) -> None:
    (tmp_path / "nested.contract").write_text(
        """
type Child:
    name: str
    score: int

type Parent:
    child: Child

agent NestedAgent() -> Parent:
    goal = "return nested output"
""".strip()
    )
    artifacts = compile_project(tmp_path)
    runner = EvalRunner(artifacts["schemas"])

    valid = await runner.evaluate(
        name="valid",
        output={"child": {"name": "ok", "score": 1}},
        output_type="Parent",
        trace=TraceRecorder(),
        expectations=["output conforms Parent"],
    )
    invalid = await runner.evaluate(
        name="invalid",
        output={"child": {"name": "bad", "score": "not an integer"}},
        output_type="Parent",
        trace=TraceRecorder(),
        expectations=["output conforms Parent"],
    )

    assert valid.passed
    assert not invalid.passed
    assert "$defs" in artifacts["schemas"]["Parent"]
    assert artifacts["schemas"]["Parent"]["$defs"]["Child"]["properties"]["score"]["type"] == "integer"


def test_lark_expression_parser_characterizes_supported_surface() -> None:
    assert parse_expectation("output conforms IncidentBrief").type_name == "IncidentBrief"
    assert parse_expectation("output.ok == true").value is True
    assert parse_expectation("output.count != 2").value == 2
    assert parse_expectation("output.message contains payment timeout").value == "payment timeout"
    assert parse_expectation("output discovers hidden_truth.likely_cause").field == "likely_cause"
    assert parse_semantic_expectation('semantic(output, "clear and grounded")').value == "clear and grounded"
    assert parse_monitor_condition('trace.contains("payment, timeout")').args == ("payment, timeout",)

    wrapped = parse_contract_expression("when(trace.tool_called(logs.search), expect(output.ok == true))")
    assert [item.kind for item in wrapped] == ["trace", "output_compare"]
    assert parse_contract_expression("forbid(tool.status_page.draft_update unless approved_by_human)")[0].args == (
        "status_page.draft_update",
    )

    with pytest.raises(ExpressionError):
        parse_expectation("trace.called_times(A, many)")


def test_trace_op_specs_cover_documented_spies() -> None:
    assert TRACE_OPS["called_times"].count_arg_index == 1
    assert TRACE_OPS["tool_called"].event_type == "tool.completed"
    assert TRACE_OPS["hosted_tool_called"].event_type == "hosted_tool.completed"
    assert TRACE_OPS["agent_called"].target_kind == "agent"
    assert TRACE_OPS["approval_granted"].target_kind == "approval_tool"
    assert TRACE_OPS["guardrail_rejected"].event_type == "guardrail.rejected"


@pytest.mark.asyncio
async def test_eval_runner_reports_failures() -> None:
    trace = TraceRecorder()
    runner = EvalRunner(
        {"Result": {"type": "object", "properties": {}}, "Wrong": {"type": "object", "required": ["x"]}}
    )

    result = await runner.evaluate(
        name="case",
        output={},
        output_type="Result",
        trace=trace,
        expectations=[
            "trace.tool_called(logs.search)",
            "output.missing == true",
            "output.absent != true",
            "trace.toool_called(logs.search)",
            "output conforms Wrong",
        ],
    )

    assert not result.passed
    assert {failure.kind for failure in result.failures} >= {"trace", "output", "unsupported"}


@pytest.mark.asyncio
async def test_eval_runner_supports_documented_trace_spies() -> None:
    trace = TraceRecorder()
    trace.record("agent.completed", agent="A")
    trace.record("tool.completed", tool="tool.x")
    trace.record("tool.completed", tool="tool.x")
    trace.record("hosted_tool.completed", tool="openai.web_search")
    trace.record("datasource.resolved", datasource="Source", produces="Thing")
    trace.record("approval.requested", tool="tool.x")
    trace.record("approval.completed", tool="tool.x", approved=True)
    trace.record("guardrail.rejected", guardrail="prompt_injection")
    runner = EvalRunner({"Result": {"type": "object", "properties": {}}})

    result = await runner.evaluate(
        name="case",
        output={},
        output_type="Result",
        trace=trace,
        expectations=[
            "trace.called(A)",
            "trace.called_once(A)",
            "trace.called_times(A, 1)",
            "trace.max_calls(A, 1)",
            "trace.called_before(A, tool.x)",
            "trace.called_after(tool.x, A)",
            "trace.tool_called(tool.x)",
            "trace.hosted_tool_called(openai.web_search)",
            "trace.agent_called(A)",
            "trace.datasource_resolved(Thing)",
            "trace.approval_requested(tool.x)",
            "trace.approval_granted(tool.x)",
            "trace.guardrail_rejected(prompt_injection)",
            "trace.not_called(missing)",
        ],
    )

    assert result.passed


@pytest.mark.asyncio
async def test_hosted_tool_spy_does_not_satisfy_host_tool_spy() -> None:
    trace = TraceRecorder()
    trace.record("hosted_tool.completed", tool="openai.web_search")
    runner = EvalRunner({"Result": {"type": "object", "properties": {}}})

    result = await runner.evaluate(
        name="case",
        output={},
        output_type="Result",
        trace=trace,
        expectations=[
            "trace.called(openai.web_search)",
            "trace.hosted_tool_called(openai.web_search)",
            "trace.tool_called(openai.web_search)",
        ],
    )

    assert not result.passed
    assert [failure.kind for failure in result.failures] == ["trace"]
    assert "openai.web_search" in result.failures[0].message


def test_monitor_violation() -> None:
    trace = TraceRecorder()
    trace.record("tool.completed", tool="status_page.draft_update")

    violations = run_monitors(
        [
            MonitorRule(
                "approval_required",
                "IncidentCommander",
                "high",
                "trace.tool_called(status_page.draft_update)",
                'trace.approval_granted("status_page.draft_update")',
            )
        ],
        trace,
    )

    assert violations[0].severity == "high"


@pytest.mark.asyncio
async def test_incident_command_harness_discovers_seeded_truth(tmp_path: Path) -> None:
    db_path = seed_incident_command(tmp_path / "fixture.sqlite")
    output, trace = await run_incident_command_harness(db_path)
    truth = load_hidden_truth(db_path)

    assert "8f31c2" in output["likely_cause"]
    assert "8f31c2" in truth["likely_cause"]
    assert trace.count("tool.completed") == 3
    assert "scenario_truth" not in " ".join(str(event.data) for event in trace.events)
