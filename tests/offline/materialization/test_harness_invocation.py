from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypedDict, Unpack, cast

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.session import get_session  # type: ignore[import-untyped]
from botocore.validate import validate_parameters  # type: ignore[import-untyped]
from pydantic import BaseModel, ValidationError

from contract4agents import materialize
from contract4agents._strict_json import json_array, json_object
from contract4agents.adapters._native_names import native_name
from contract4agents.adapters.agentcore_harness import (
    AgentCoreHarnessMaterializationProvider,
    HarnessAgent,
    HarnessApproval,
    HarnessCompleted,
    HarnessFailed,
    HarnessInterrupted,
    HarnessPendingCall,
    HarnessRunContext,
    generate_harness_configuration,
    verify_harness_deployment,
)
from contract4agents.ir import semantic_id
from contract4agents.materialization import MaterializationError, MaterializedSystem
from contract4agents.materialization import _harness_invocation as harness_invocation
from contract4agents.materialization._harness_configuration import frozen_object
from contract4agents.materialization._harness_models import HarnessResult
from contract4agents.tracing import NormalizedTrace, RecordingNormalizedTraceSink, TraceAttempt, TraceEvent
from tests.support.agentcore_harness import (
    FakeHarnessControlClient,
    FakeHarnessRuntimeClient,
    success_stream,
    tool_stream,
    write_project,
)

INPUT = {"request": {"question": "synthetic question"}}
OUTPUT = {"answer": "synthetic answer", "evidence_ids": ["source-1"]}


class _TransportConfiguration(Protocol):
    read_timeout: int
    retries: dict[str, object]


class _ContextChanges(TypedDict, total=False):
    session_id: str
    principal_id: str
    authorization_context: str
    run_id: str
    thread_id: str
    attempt: TraceAttempt
    parent_event_id: str | None


def context(**changes: Unpack[_ContextChanges]) -> HarnessRunContext:
    return replace(HarnessRunContext(
        session_id="00000000-0000-0000-0000-000000000001",
        principal_id="test-user",
        authorization_context="test-account",
        run_id="run-1",
        thread_id="thread-1",
        attempt=TraceAttempt("invocation-1", "attempt-1", 1),
    ), **changes)


def agent_for(system: MaterializedSystem, name: str) -> HarnessAgent:
    agent = system.agents[name]
    assert isinstance(agent, HarnessAgent)
    return agent


def request_content(client: FakeHarnessRuntimeClient, index: int) -> Mapping[str, object]:
    message = json_object("message", json_array("messages", client.requests[index]["messages"])[0])
    return json_object("content", json_array("content", message["content"])[0])


def pending_call(result: HarnessResult) -> HarnessPendingCall:
    assert isinstance(result, HarnessInterrupted)
    return result.pending


def failure(result: HarnessResult) -> HarnessFailed:
    assert isinstance(result, HarnessFailed)
    return result


def system(
    tmp_path: Path, *, approval: bool = False, delegate: bool = False,
    streams: list[list[dict[str, object]]] | None = None, request_fields: str = "",
    include_tool: bool = True, output_mode: str | None = None,
    timeout_seconds: int | None = None,
    result_declaration: str | None = None,
    output_type: str = "Result",
) -> tuple[MaterializedSystem, FakeHarnessRuntimeClient, RecordingNormalizedTraceSink]:
    root = write_project(
        tmp_path, approval=approval, delegate=delegate, request_fields=request_fields,
        include_tool=include_tool,
        result_declaration=result_declaration,
        output_type=output_type,
    )
    if output_mode is not None:
        target = root / "contract4agents.targets.toml"
        target.write_text(target.read_text() + f'\noutput_mode = "{output_mode}"\n')
    if timeout_seconds is not None:
        target = root / "contract4agents.targets.toml"
        target.write_text(target.read_text() + f'\ntimeout_seconds = {timeout_seconds}\n')
    configuration = generate_harness_configuration(root, target="agentcore_harness", profile="test")
    deployments = {}
    for index, name in enumerate(configuration.agents):
        control = FakeHarnessControlClient(configuration, name)
        control.harness_id = f"TestAgent{index}-abcdefghij"
        control.harness_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:harness/" + control.harness_id
        deployments[name] = verify_harness_deployment(
            configuration,
            name,
            harness_arn=control.harness_arn,
            endpoint_name=control.endpoint_name,
            client=control,
        )
    client = FakeHarnessRuntimeClient(streams)
    sink = RecordingNormalizedTraceSink()
    result = materialize(
        root,
        target="agentcore_harness",
        profile="test",
        provider=AgentCoreHarnessMaterializationProvider(deployments=deployments, client=client),
        normalized_trace_sink=sink,
    )
    return result, client, sink


def test_materialization_requires_deployment_evidence(tmp_path: Path) -> None:
    write_project(tmp_path)
    with pytest.raises(MaterializationError, match="verified deployment"):
        materialize(tmp_path, target="agentcore_harness", profile="test")


def test_generated_types_and_actual_aws_invocation_shape_are_preserved(tmp_path: Path) -> None:
    result, client, sink = system(tmp_path, streams=[success_stream(json.dumps(OUTPUT))])
    handle = agent_for(result, "Analyst")
    assert isinstance(handle, HarnessAgent)
    assert result.validation.complete
    output = handle.invoke(INPUT, context=context())
    assert isinstance(output, HarnessCompleted)
    assert result.validate_output_for_agent(handle, output.value) is output.value
    request_shape = get_session().get_service_model("bedrock-agentcore").operation_model("InvokeHarness").input_shape
    validate_parameters(client.requests[0], request_shape)
    assert client.requests[0]["allowedTools"] == [f"@*/{name}" for name in handle.configuration.tools]
    trace = NormalizedTrace(tuple(sink.events))
    assert "synthetic question" not in json.dumps([event.to_dict() for event in trace.events])
    assert {e.event_type for e in trace.events} >= {"output.accepted", "provider.outcome.reported"}
    assert all(e.context.plan_digest == result.plan.plan_digest for e in trace.events)
    assert all("deployment" in e.provenance for e in trace.events)


@pytest.mark.parametrize("final_text", ['{"answer":"ready","evidence_ids":["source-1"]}',
                                       '{"answer":"x","evidence_ids":[]}',
                                       'The answer is {"answer":"ready","evidence_ids":[]}'])
def test_schema_output_preserves_tool_continuation_and_strict_final_validation(
    tmp_path: Path, final_text: str,
) -> None:
    result, client, sink = system(
        tmp_path, output_mode="schema",
        streams=[
            tool_stream(native_name("tool", semantic_id("tool", "records.lookup")), INPUT),
            success_stream(final_text),
        ],
        result_declaration='type Result:\n    answer: string(min_length=2)\n    evidence_ids: list[string]',
    )
    handle = agent_for(result, "Analyst")
    scope = context()
    interrupted = handle.invoke(INPUT, context=scope)
    assert isinstance(interrupted, HarnessInterrupted)
    completed = handle.resume(interrupted.pending, OUTPUT, context=scope)
    if final_text.startswith('{"answer":"ready"'):
        assert isinstance(completed, HarnessCompleted)
        assert result.validate_output_for_agent(handle, completed.value) is completed.value
        assert sum(event.event_type == "output.accepted" for event in sink.events) == 1
    else:
        assert failure(completed).reason == "invalid_output"
        assert not any(event.event_type == "output.accepted" for event in sink.events)
    assert len(client.requests) == 2
    for request in client.requests:
        model = json_object("model", request["model"])
        bedrock = json_object("bedrock", model["bedrockModelConfig"])
        assert "outputConfig" in json_object("additional parameters", bedrock["additionalParams"])
        assert request["allowedTools"] == [f"@*/{name}" for name in handle.configuration.tools]
    assert client.requests[0]["model"] == client.requests[1]["model"]


def test_default_runtime_transport_uses_the_configured_timeout_without_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, client, _ = system(
        tmp_path, output_mode="schema", timeout_seconds=180, streams=[success_stream(json.dumps(OUTPUT))],
    )
    handle = replace(agent_for(result, "Analyst"), client=None)
    configurations: list[_TransportConfiguration] = []

    def create_client(service: str, *, region_name: str, config: object) -> FakeHarnessRuntimeClient:
        assert service == "bedrock-agentcore" and region_name == "us-west-2"
        configurations.append(cast(_TransportConfiguration, config))
        return client

    monkeypatch.setattr(boto3, "client", create_client)
    assert isinstance(handle.invoke(INPUT, context=context()), HarnessCompleted)
    assert len(configurations) == 1
    assert configurations[0].read_timeout == 180
    assert configurations[0].retries == {"total_max_attempts": 1}
    assert client.requests[0]["timeoutSeconds"] == 180


def _adjacent_calls(
    second_name: str | None = None, second_arguments: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    name = native_name("tool", semantic_id("tool", "records.lookup"))
    first = tool_stream(name, INPUT, "call-first")
    second = tool_stream(second_name or name, second_arguments or INPUT, "call-second")
    following: list[dict[str, object]] = []
    for event in second[1:-1]:
        key, payload = next(iter(event.items()))
        following.append({key: {**json_object("block", payload), "contentBlockIndex": 1}})
    return first[:-1] + following + first[-1:]


@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.parametrize("approval_required", [False, True])
def test_adjacent_calls_collect_each_host_result_and_submit_one_ordered_batch(
    tmp_path: Path, recover: bool, approval_required: bool,
) -> None:
    result, client, sink = system(
        tmp_path, approval=approval_required,
        streams=[_adjacent_calls(), success_stream(json.dumps(OUTPUT))],
    )
    handle = agent_for(result, "Analyst")
    scope = context()
    first = pending_call(handle.invoke(INPUT, context=scope))
    assert first.tool_use_id == "call-first" and first.batch is not None
    decision = HarnessApproval(first.call_digest, True, "approval:first") if approval_required else None
    if decision is not None:
        handle.record_approval(first, decision, context=scope)
    second = pending_call(handle.resume(first, OUTPUT, context=scope, approval=decision))
    assert second.tool_use_id == "call-second" and len(client.requests) == 1
    assert not any(event.event_type == "output.accepted" for event in sink.events)
    assert handle.tool_arguments(second, context=scope) == INPUT
    if recover:
        second = HarnessPendingCall.from_dict(second.to_dict())
        handle = HarnessAgent(handle.configuration, handle.deployment, client, sink)
        handle.restore_pending(second, context=scope)
        with pytest.raises(ValueError, match="already been submitted"):
            handle.restore_pending(first, context=scope)
    decision = HarnessApproval(second.call_digest, True, "approval:second") if approval_required else None
    if decision is not None:
        with pytest.raises(ValueError, match="explicit application approval"):
            handle.resume(second, OUTPUT, context=scope)
        handle.record_approval(second, decision, context=scope)
    completed = handle.resume(second, OUTPUT, context=scope, approval=decision)
    assert isinstance(completed, HarnessCompleted) and len(client.requests) == 2
    message = json_object("message", json_array("messages", client.requests[1]["messages"])[0])
    responses = [json_object("result", item)["toolResult"] for item in json_array("content", message["content"])]
    assert [json_object("tool result", item)["toolUseId"] for item in responses] == ["call-first", "call-second"]
    for item in responses:
        content = json_array("content", json_object("result", item)["content"])
        assert json.loads(str(json_object("text", content[0])["text"])) == OUTPUT
    assert sum(event.event_type == "agent.started" for event in sink.events) == 2
    assert sum(event.event_type == "tool.requested" for event in sink.events) == 2
    assert sum(event.event_type == "output.accepted" for event in sink.events) == 1
    with pytest.raises(ValueError, match="already submitted"):
        handle.resume(second, OUTPUT, context=scope, approval=decision)


@pytest.mark.parametrize("later_name,later_arguments,reason", [
    ("ungranted", INPUT, "ungranted_tool"),
    (None, {"request": {"question": 9}}, "invalid_tool_input"),
])
def test_invalid_later_call_rejects_the_whole_handoff_before_host_execution(
    tmp_path: Path, later_name: str | None, later_arguments: Mapping[str, object], reason: str,
) -> None:
    result, client, sink = system(tmp_path, streams=[_adjacent_calls(later_name, later_arguments)])
    rejected = agent_for(result, "Analyst").invoke(INPUT, context=context())
    assert failure(rejected).reason == reason
    assert len(client.requests) == 1
    assert not any(event.event_type in {"tool.requested", "approval.requested"} for event in sink.events)


def test_batch_recovery_rejects_tampered_prior_result_before_any_request(tmp_path: Path) -> None:
    result, client, _ = system(tmp_path, streams=[_adjacent_calls()])
    handle = agent_for(result, "Analyst")
    scope = context()
    first = pending_call(handle.invoke(INPUT, context=scope))
    second = pending_call(handle.resume(first, OUTPUT, context=scope))
    raw = json.loads(json.dumps(second.to_dict()))
    raw["batch"]["results"][0]["response"]["content"][0]["text"] = '{"answer":7}'
    restored = HarnessPendingCall.from_dict(raw)
    fresh = HarnessAgent(handle.configuration, handle.deployment, client)
    with pytest.raises(ValidationError):
        fresh.restore_pending(restored, context=scope)
    assert len(client.requests) == 1


def test_batch_recovery_preserves_denial_and_requires_the_next_approval(tmp_path: Path) -> None:
    result, client, _ = system(
        tmp_path, approval=True, streams=[_adjacent_calls(), success_stream(json.dumps(OUTPUT))],
    )
    handle = agent_for(result, "Analyst")
    scope = context()
    first = pending_call(handle.invoke(INPUT, context=scope))
    denied = HarnessApproval(first.call_digest, False, "approval:denied")
    handle.record_approval(first, denied, context=scope)
    second = pending_call(handle.resume(first, context=scope, approval=denied))
    assert len(client.requests) == 1
    fresh = HarnessAgent(handle.configuration, handle.deployment, client)
    fresh.restore_pending(HarnessPendingCall.from_dict(second.to_dict()), context=scope)
    with pytest.raises(ValueError, match="explicit application approval"):
        fresh.resume(second, OUTPUT, context=scope)
    approved = HarnessApproval(second.call_digest, True, "approval:next")
    fresh.record_approval(second, approved, context=scope)
    assert isinstance(fresh.resume(second, OUTPUT, context=scope, approval=approved), HarnessCompleted)
    message = json_object("message", json_array("messages", client.requests[1]["messages"])[0])
    responses = [json_object("result", item)["toolResult"] for item in json_array("content", message["content"])]
    assert [json_object("result", item)["status"] for item in responses] == ["error", "success"]


def test_batch_recovery_rejects_changed_historical_approval(tmp_path: Path) -> None:
    result, client, _ = system(tmp_path, approval=True, streams=[_adjacent_calls()])
    handle = agent_for(result, "Analyst")
    scope = context()
    first = pending_call(handle.invoke(INPUT, context=scope))
    approved = HarnessApproval(first.call_digest, True, "approval:prior")
    handle.record_approval(first, approved, context=scope)
    second = pending_call(handle.resume(first, OUTPUT, context=scope, approval=approved))
    raw = json.loads(json.dumps(second.to_dict()))
    raw["batch"]["results"][0]["approval"]["call_digest"] = "sha256:" + "0" * 64
    fresh = HarnessAgent(handle.configuration, handle.deployment, client)
    with pytest.raises(ValueError, match="Buffered approval"):
        fresh.restore_pending(HarnessPendingCall.from_dict(raw), context=scope)
    assert len(client.requests) == 1


def test_buffered_handoff_remains_reserved_during_trace_delivery(tmp_path: Path) -> None:
    result, client, _ = system(tmp_path, streams=[_adjacent_calls()])
    original = agent_for(result, "Analyst")
    scope = context()
    attempts: list[bool] = []

    class RecoverySink(RecordingNormalizedTraceSink):
        def emit(self, event: TraceEvent) -> None:
            super().emit(event)
            if event.event_type == "tool.result_submitted" and event.data.get("submission_phase") == "buffered":
                with pytest.raises(ValueError, match="Another pending call"):
                    handle.restore_pending(conflicting, context=scope)
                attempts.append(True)

    handle = HarnessAgent(original.configuration, original.deployment, client, RecoverySink())
    first = pending_call(handle.invoke(INPUT, context=scope))
    conflicting = replace(first, batch=None, tool_use_id="other-call", event_id="other-event")
    second = pending_call(handle.resume(first, OUTPUT, context=scope))
    assert attempts == [True] and second.tool_use_id == "call-second"
    assert handle.tool_arguments(second, context=scope) == INPUT
    assert len(client.requests) == 1


def test_native_result_tool_completes_only_a_valid_value_envelope(tmp_path: Path) -> None:
    root = write_project(tmp_path)
    target = root / "contract4agents.targets.toml"
    target.write_text(target.read_text() + '\noutput_mode = "tool"\n')
    configuration = generate_harness_configuration(root, target="agentcore_harness", profile="test")
    controls = FakeHarnessControlClient(configuration)
    deployment = verify_harness_deployment(
        configuration,
        "Analyst",
        harness_arn=controls.harness_arn,
        endpoint_name=controls.endpoint_name,
        client=controls,
    )
    result_name = configuration.agents["Analyst"].result_tool_name or ""
    client = FakeHarnessRuntimeClient([tool_stream(result_name, {"value": OUTPUT})])
    handle = HarnessAgent(configuration.agents["Analyst"], deployment, client)
    output = handle.invoke(INPUT, context=context())
    assert isinstance(output, HarnessCompleted)
    assert output.value.answer == OUTPUT["answer"]  # type: ignore[attr-defined]

    malformed = FakeHarnessRuntimeClient(
        [tool_stream(result_name, {"value": OUTPUT, "extra": True})]
    )
    failed = HarnessAgent(configuration.agents["Analyst"], deployment, malformed).invoke(INPUT, context=context())
    assert isinstance(failed, HarnessFailed) and failed.reason == "invalid_output"


@pytest.mark.parametrize("before_tool", [True, False])
@pytest.mark.parametrize("valid", [True, False])
def test_result_tool_validates_arguments_independently_of_commentary(
    tmp_path: Path, before_tool: bool, valid: bool,
) -> None:
    result_name = native_name("result", semantic_id("agent", "Analyst"))
    value = OUTPUT if valid else {"answer": 7, "evidence_ids": []}
    events = tool_stream(result_name, {"value": value})
    position = 1 if before_tool else len(events) - 1
    events[position:position] = [
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"text": "The result is ready."}}},
        {"contentBlockStop": {"contentBlockIndex": 1}},
    ]
    result, _, sink = system(tmp_path, streams=[events], output_mode="tool")

    outcome = agent_for(result, "Analyst").invoke(INPUT, context=context())

    if valid:
        assert isinstance(outcome, HarnessCompleted)
        assert outcome.value.answer == OUTPUT["answer"]  # type: ignore[attr-defined]
    else:
        assert isinstance(outcome, HarnessFailed) and outcome.reason == "invalid_output"
    assert "The result is ready." not in json.dumps([event.to_dict() for event in sink.events])


def test_tool_output_mode_refuses_a_result_mixed_with_another_call(tmp_path: Path) -> None:
    result_name = native_name("result", semantic_id("agent", "Analyst"))
    events = tool_stream(result_name, {"value": OUTPUT})
    events[-1:-1] = [
        {
            "contentBlockStart": {
                "contentBlockIndex": 1,
                "start": {"toolUse": {"name": "records_lookup", "toolUseId": "call-2"}},
            }
        },
        {
            "contentBlockDelta": {
                "contentBlockIndex": 1,
                "delta": {"toolUse": {"input": json.dumps({"request": INPUT["request"]})}},
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 1}},
    ]
    result, _, _ = system(tmp_path, streams=[events], output_mode="tool")

    outcome = agent_for(result, "Analyst").invoke(INPUT, context=context())

    assert isinstance(outcome, HarnessFailed)
    assert outcome.reason == "invalid_output"


def test_native_result_tool_completes_nested_structural_enum_value(tmp_path: Path) -> None:
    result_name = native_name("result", semantic_id("agent", "Analyst"))
    value = {
        "answer": "synthetic answer",
        "citations": [{"identifier": "source-1", "kind": "paper"}],
    }
    nested_result = (
        'enum CitationKind:\n    "paper"\n    "report"\n\n'
        "type Citation:\n    identifier: string\n    kind: CitationKind\n\n"
        "type Result:\n    answer: string\n    citations: list[Citation]"
    )
    result, _, _ = system(
        tmp_path,
        streams=[tool_stream(result_name, {"value": value})],
        output_mode="tool",
        result_declaration=nested_result,
    )
    completed = agent_for(result, "Analyst").invoke(INPUT, context=context())
    assert isinstance(completed, HarnessCompleted)
    assert completed.value.model_dump(mode="json") == value  # type: ignore[attr-defined]


def test_native_result_tool_completes_scalar_timezone_aware_datetime(tmp_path: Path) -> None:
    result_name = native_name("result", semantic_id("agent", "Analyst"))
    timestamp = "2026-10-28T12:34:56Z"
    result, _, _ = system(
        tmp_path,
        streams=[tool_stream(result_name, {"value": timestamp})],
        output_mode="tool",
        output_type="datetime",
    )

    completed = agent_for(result, "Analyst").invoke(INPUT, context=context())

    assert isinstance(completed, HarnessCompleted)
    assert isinstance(completed.value, datetime)
    assert completed.value.isoformat() == "2026-10-28T12:34:56+00:00"


def test_native_result_tool_completes_nested_timezone_aware_datetime(tmp_path: Path) -> None:
    result_name = native_name("result", semantic_id("agent", "Analyst"))
    value = {"metadata": {"created_at": "2026-10-28T12:34:56+02:00"}}
    result, _, _ = system(
        tmp_path,
        streams=[tool_stream(result_name, {"value": value})],
        output_mode="tool",
        result_declaration="type Metadata:\n    created_at: datetime\n\ntype Result:\n    metadata: Metadata",
    )

    completed = agent_for(result, "Analyst").invoke(INPUT, context=context())

    assert isinstance(completed, HarnessCompleted)
    assert cast(BaseModel, completed.value).model_dump(mode="json") == value


@pytest.mark.parametrize(
    "value",
    [
        "not-a-date",
        42,
    ],
)
def test_native_result_tool_rejects_invalid_datetime_values(tmp_path: Path, value: object) -> None:
    result_name = native_name("result", semantic_id("agent", "Analyst"))
    result, _, _ = system(
        tmp_path,
        streams=[tool_stream(result_name, {"value": value})],
        output_mode="tool",
        output_type="datetime",
    )

    outcome = agent_for(result, "Analyst").invoke(INPUT, context=context())

    assert isinstance(outcome, HarnessFailed)
    assert outcome.reason == "invalid_output"


def test_result_tool_supports_zero_application_tools_and_records_output_mode(tmp_path: Path) -> None:
    result_name = native_name("result", semantic_id("agent", "Analyst"))
    result, _, _ = system(
        tmp_path,
        streams=[tool_stream(result_name, {"value": OUTPUT})],
        include_tool=False,
        output_mode="tool",
    )
    handle = agent_for(result, "Analyst")
    assert tuple(handle.configuration.tools) == ()
    assert handle.configuration.result_tool_name == result_name
    completed = handle.invoke(INPUT, context=context())
    assert isinstance(completed, HarnessCompleted)
    fact = next(
        item for item in result.validation.configuration_conformance
        if item.property_path == "agent.output_mode"
    )
    assert fact.planned_value == "tool"
    assert fact.observed_value == "tool"
    assert fact.status == "passed"


def test_harness_schema_evidence_is_accepted_by_assurance_without_inventing_missing_evidence(tmp_path: Path) -> None:
    from contract4agents.assurance import assemble_assurance_bundle
    from contract4agents.ir import semantic_id

    result, _, _ = system(tmp_path, delegate=True)
    boundaries = {(item.semantic_id, item.boundary) for item in result.validation.schema_conformance}
    assert (semantic_id("agent", "Analyst"), "host_structural_output") in boundaries
    assert (semantic_id("agent", "Parent"), "host_structural_output") in boundaries
    assert (semantic_id("grant", "Analyst", "records.lookup"), "tool_input") in boundaries
    assert (semantic_id("edge", "analyze"), "delegate_input") in boundaries
    evidence = type(result.validation).from_json(result.validation.to_json())
    bundle = assemble_assurance_bundle(
        result,
        normalized_trace_jsonl=None,
        control_results=None,
        eval_results=None,
        provenance=None,
        materialization_evidence=evidence,
    )
    codes = {item.code for item in bundle.diagnostics}
    assert "BUNDLE017" not in codes
    assert "BUNDLE016" not in codes
    assert "BUNDLE001" in codes
    assert not bundle.complete


@pytest.mark.parametrize("inputs", [{}, {"request": {"question": 7}}, {**INPUT, "tools": []}])
def test_invalid_input_never_reaches_aws(tmp_path: Path, inputs: object) -> None:
    result, client, _ = system(tmp_path)
    with pytest.raises((ValueError, ValidationError)):
        agent_for(result, "Analyst").invoke(inputs, context=context())  # type: ignore[arg-type]
    assert client.requests == []


@pytest.mark.parametrize("override", [{"allowedTools": ["*"]}, {"systemPrompt": []}, {"model": {}}, {"skills": []}])
def test_overrides_cannot_bypass_the_plan(tmp_path: Path, override: object) -> None:
    result, client, _ = system(tmp_path)
    with pytest.raises(ValueError, match="profile"):
        agent_for(result, "Analyst").invoke(INPUT, context=context(), overrides=override)  # type: ignore[arg-type]
    assert not client.requests


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"answer": 5,"evidence_ids":[]}',
        '{"answer":"a","answer":"b","evidence_ids":[]}',
        '```json\n{"answer":"a","evidence_ids":[]}\n```',
    ],
)
def test_invalid_final_output_is_a_typed_failure(tmp_path: Path, text: str) -> None:
    result, _, sink = system(tmp_path, streams=[success_stream(text)])
    output = agent_for(result, "Analyst").invoke(INPUT, context=context())
    assert isinstance(output, HarnessFailed) and output.reason == "invalid_output"
    assert "output.accepted" not in {event.event_type for event in sink.events}


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("content_filtered", "refusal"),
        ("timeout_exceeded", "timeout"),
        ("max_tokens", "token_limit"),
        ("hook_stopped", "hook_stopped"),
        ("partial_turn", "incomplete_execution"),
    ],
)
def test_nonfinal_provider_states_are_not_success(tmp_path: Path, reason: str, expected: str) -> None:
    events = success_stream()
    events[-1] = {"messageStop": {"stopReason": reason}}
    result, _, _ = system(tmp_path, streams=[events])
    output = agent_for(result, "Analyst").invoke(INPUT, context=context())
    assert isinstance(output, HarnessFailed) and output.reason == expected


def test_transport_failure_is_explicit_and_does_not_retry(tmp_path: Path) -> None:
    result, client, sink = system(tmp_path)
    output = agent_for(result, "Analyst").invoke(INPUT, context=context())
    assert isinstance(output, HarnessFailed) and output.reason == "transport_failure"
    assert len(client.requests) == 1
    assert "output.accepted" not in {event.event_type for event in sink.events}


@pytest.mark.parametrize("metadata", [[], {"RequestId": 42}])
def test_invalid_response_metadata_closes_the_acquired_stream(tmp_path: Path, metadata: object) -> None:
    result, _, _ = system(tmp_path)
    original = agent_for(result, "Analyst")

    class ClosingStream:
        closed = False

        def __iter__(self) -> Iterator[dict[str, object]]:
            return iter(success_stream())

        def close(self) -> None:
            self.closed = True

    events = ClosingStream()

    class InvalidMetadata:
        def invoke_harness(self, **kwargs: object) -> Mapping[str, object]:
            return {"stream": events, "ResponseMetadata": metadata}

    handle = HarnessAgent(original.configuration, original.deployment, InvalidMetadata())
    output = handle.invoke(INPUT, context=context())

    assert isinstance(output, HarnessFailed) and output.reason == "transport_failure"
    assert output.response_received
    assert events.closed


@pytest.mark.parametrize(
    ("error_code", "expected"),
    [
        ("AccessDeniedException", "authorization_failure"),
        ("ExpiredTokenException", "authentication_failure"),
        ("ThrottlingException", "rate_limited"),
    ],
)
def test_aws_failure_classification_retains_only_available_response_facts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, error_code: str, expected: str,
) -> None:
    from botocore.exceptions import ClientError  # type: ignore[import-untyped]

    caplog.set_level(logging.ERROR, logger=harness_invocation.__name__)
    result, _, sink = system(tmp_path)
    original = agent_for(result, "Analyst")
    request_id = "123e4567-e89b-12d3-a456-426614174000"

    class Rejected:
        def invoke_harness(self, **kwargs: object) -> Mapping[str, object]:
            raise ClientError({
                "Error": {"Code": error_code, "Message": "secret provider text"},
                "ResponseMetadata": {"RequestId": request_id},
            }, "InvokeHarness")

    handle = HarnessAgent(original.configuration, original.deployment, Rejected(), sink)
    output = handle.invoke(INPUT, context=context())
    assert output == HarnessFailed(expected, None, None, True)
    facts = [
        json_object("evidence", e.data["evidence"])
        for e in sink.events if e.event_type == "provider.outcome.reported"
    ]
    assert facts[0]["response_received"] is True
    assert "secret provider text" not in json.dumps([e.to_dict() for e in sink.events])
    event = _failure_event(caplog)
    assert event["phase"] == "request" and event["exception_class"] == "ClientError"
    assert event["aws_error_code"] == error_code
    assert event["request_id"] == request_id
    assert "secret provider text" not in caplog.text


def test_timeout_before_response_does_not_claim_a_provider_response(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger=harness_invocation.__name__)
    result, _, sink = system(tmp_path)
    original = agent_for(result, "Analyst")

    class TimedOut:
        def invoke_harness(self, **kwargs: object) -> Mapping[str, object]:
            raise TimeoutError("provider timeout")

    handle = HarnessAgent(original.configuration, original.deployment, TimedOut(), sink)
    output = handle.invoke(INPUT, context=context())
    assert isinstance(output, HarnessFailed) and output.reason == "timeout"
    facts = [
        json_object("evidence", e.data["evidence"])
        for e in sink.events if e.event_type == "provider.outcome.reported"
    ]
    assert facts[0]["response_received"] is False
    assert facts[0]["phase"] == "transport"
    event = _failure_event(caplog)
    assert event["phase"] == "request" and event["exception_class"] == "TimeoutError"


class _DiagnosticStream:
    def __init__(
        self, events: list[dict[str, object]], *, read_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.read_error = read_error
        self.close_error = close_error
        self.close_count = 0

    def __iter__(self) -> Iterator[dict[str, object]]:
        yield from self.events
        if self.read_error is not None:
            raise self.read_error

    def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


class _DiagnosticClient:
    def __init__(self, response: Mapping[str, object], error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[dict[str, object]] = []

    def invoke_harness(self, **kwargs: object) -> Mapping[str, object]:
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _failure_event(caplog: pytest.LogCaptureFixture) -> dict[str, object]:
    records = [record for record in caplog.records if record.name == harness_invocation.__name__]
    assert len(records) == 1
    assert records[0].exc_info is None and records[0].stack_info is None
    event = json_object("event", json.loads(records[0].getMessage()))
    assert event["event"] == "agentcore_harness_request_failure"
    return dict(event)


@pytest.mark.parametrize("code", ["internalServerException", "validationException", "runtimeClientError"])
def test_modeled_aws_stream_errors_have_content_free_diagnostics(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, code: str,
) -> None:
    from botocore.eventstream import EventStream, EventStreamMessage  # type: ignore[import-untyped]
    from botocore.parsers import EventStreamJSONParser  # type: ignore[import-untyped]

    caplog.set_level(logging.ERROR, logger=harness_invocation.__name__)
    shape = get_session().get_service_model("bedrock-agentcore").operation_model("InvokeHarness").output_shape
    stream = EventStream(None, shape.members["stream"], EventStreamJSONParser(), "InvokeHarness")
    message = EventStreamMessage(None, {":message-type": "exception", ":exception-type": code},
                                 b'{"message":"private-provider-marker","reason":"synthetic"}', None)

    class ModeledStream(_DiagnosticStream):
        def __iter__(self) -> Iterator[dict[str, object]]:
            yield dict(json_object("event", stream._parse_event(message)))

    events = ModeledStream([])
    request_id = "123e4567-e89b-12d3-a456-426614174000"
    client = _DiagnosticClient({"stream": events, "ResponseMetadata": {"RequestId": request_id}})
    result, _, sink = system(tmp_path)
    original = agent_for(result, "Analyst")
    handle = HarnessAgent(original.configuration, original.deployment, client, sink)
    scope = context(run_id=request_id, attempt=TraceAttempt(request_id, "attempt-1", 1))
    output = handle.invoke(INPUT, context=scope)

    assert output == HarnessFailed("transport_failure", request_id, None, True)
    assert _failure_event(caplog) == {
        "event": "agentcore_harness_request_failure", "phase": "stream_read",
        "exception_class": "EventStreamError", "aws_error_code": code,
        "request_id": request_id, "run_id": request_id, "invocation_id": request_id,
    }
    assert events.close_count == 1 and len(client.requests) == 1
    assert "private-provider-marker" not in caplog.text
    assert "synthetic question" not in caplog.text
    assert "output.accepted" not in {event.event_type for event in sink.events}


@pytest.mark.parametrize(
    ("trigger", "phase", "error_class"),
    [("read", "stream_read", "ValueError"), ("size", "stream_read", "ValueError"),
     ("close", "stream_close", "ValueError"), ("read_and_close", "stream_close", "TypeError"),
     ("metadata", "response_validation", "TypeError")],
)
def test_failure_phase_preserves_read_error_after_close_and_reports_close_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, trigger: str, phase: str, error_class: str,
) -> None:
    caplog.set_level(logging.ERROR, logger=harness_invocation.__name__)
    events = _DiagnosticStream(
        success_stream(),
        read_error=ValueError("private-read-marker") if trigger in {"read", "read_and_close"} else None,
        close_error=TypeError("private-close-marker") if trigger == "read_and_close" else
        ValueError("private-close-marker") if trigger == "close" else None,
    )
    if trigger == "size":
        events.events = [{"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "x" * (8 * 1024 * 1024)}}}]
    metadata: object = {"RequestId": 42} if trigger == "metadata" else {}
    client = _DiagnosticClient({"stream": events, "ResponseMetadata": metadata})
    result, _, _ = system(tmp_path)
    original = agent_for(result, "Analyst")
    output = HarnessAgent(original.configuration, original.deployment, client).invoke(INPUT, context=context())

    assert output == HarnessFailed("transport_failure", None, None, True)
    event = _failure_event(caplog)
    assert event["phase"] == phase and event["exception_class"] == error_class
    assert event["aws_error_code"] is None
    assert events.close_count == 1 and len(client.requests) == 1
    assert "private-read-marker" not in caplog.text and "private-close-marker" not in caplog.text


@pytest.mark.parametrize(
    ("identity", "expected"),
    [("123e4567-e89b-12d3-a456-426614174000", "123e4567-e89b-12d3-a456-426614174000"),
     ("123E4567-E89B-12D3-A456-426614174000", None),
     ("{123e4567-e89b-12d3-a456-426614174000}", None),
     ("urn:uuid:123e4567-e89b-12d3-a456-426614174000", None),
     ("123e4567e89b12d3a456426614174000", None), ("private-identity-marker", None)],
)
def test_failure_diagnostics_only_log_canonical_uuid_correlations(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, identity: str, expected: str | None,
) -> None:
    caplog.set_level(logging.ERROR, logger=harness_invocation.__name__)
    events = _DiagnosticStream([], read_error=ValueError("private-error-marker"))
    client = _DiagnosticClient({"stream": events, "ResponseMetadata": {"RequestId": identity}})
    result, _, _ = system(tmp_path)
    original = agent_for(result, "Analyst")
    scope = context(run_id=identity, attempt=TraceAttempt(identity, "attempt-1", 1))
    output = HarnessAgent(original.configuration, original.deployment, client).invoke(INPUT, context=scope)

    assert output == HarnessFailed("transport_failure", identity, None, True)
    event = _failure_event(caplog)
    assert [event[key] for key in ("request_id", "run_id", "invocation_id")] == [expected] * 3
    assert "private-identity-marker" not in caplog.text
    assert events.close_count == 1 and len(client.requests) == 1


def test_unknown_exception_class_and_aws_code_do_not_leak_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger=harness_invocation.__name__)
    secret = "private-unknown-marker\ncredential=not-a-real-secret"
    error_type = type(secret, (Exception,), {"response": {"Error": {"Code": secret, "Message": secret}}})
    client = _DiagnosticClient({}, error_type(secret))
    result, _, sink = system(tmp_path)
    original = agent_for(result, "Analyst")
    output = HarnessAgent(original.configuration, original.deployment, client, sink).invoke(INPUT, context=context())

    assert output == HarnessFailed("transport_failure", None, None, True)
    assert _failure_event(caplog) == {
        "event": "agentcore_harness_request_failure", "phase": "request",
        "exception_class": "other", "aws_error_code": "other",
        "request_id": None, "run_id": None, "invocation_id": None,
    }
    assert "private-unknown-marker" not in caplog.text and "credential=" not in caplog.text
    assert "synthetic question" not in caplog.text and len(client.requests) == 1


@pytest.mark.parametrize(
    ("response", "response_received"),
    [(None, False), ([], False), ("private-response-marker", False),
     ({"ResponseMetadata": []}, True), ({"ResponseMetadata": "private-metadata-marker"}, True),
     ({"ResponseMetadata": {"RequestId": 42}}, True),
     ({"ResponseMetadata": {"RequestId": ["private-id-marker"]}}, True),
     ({"ResponseMetadata": {"RequestId": "private-id-marker\n"}}, True),
     ({"ResponseMetadata": {"RequestId": "123E4567-E89B-12D3-A456-426614174000"}}, True)],
)
def test_malformed_aws_error_request_metadata_is_omitted_without_changing_result(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, response: object, response_received: bool,
) -> None:
    caplog.set_level(logging.ERROR, logger=harness_invocation.__name__)

    class InvalidResponse(Exception):
        def __init__(self) -> None:
            self.response = response
            super().__init__("private-exception-marker")

    result, _, _ = system(tmp_path)
    original = agent_for(result, "Analyst")
    client = _DiagnosticClient({}, InvalidResponse())
    output = HarnessAgent(original.configuration, original.deployment, client).invoke(INPUT, context=context())

    assert output == HarnessFailed("transport_failure", None, None, response_received)
    event = _failure_event(caplog)
    assert event["request_id"] is None
    assert "private-" not in caplog.text and len(client.requests) == 1


@pytest.mark.parametrize("local_request_id", ["123e4567-e89b-12d3-a456-426614174000", "unsafe-local-id"])
def test_diagnostic_request_id_prefers_safe_local_id_then_safe_error_metadata(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, local_request_id: str,
) -> None:
    from botocore.exceptions import ClientError

    caplog.set_level(logging.ERROR, logger=harness_invocation.__name__)
    fallback_id = "123e4567-e89b-12d3-a456-426614174001"
    error = ClientError({
        "Error": {"Code": "runtimeClientError", "Message": "private-provider-marker"},
        "ResponseMetadata": {"RequestId": fallback_id},
    }, "InvokeHarness")
    events = _DiagnosticStream([], read_error=error)
    client = _DiagnosticClient({"stream": events, "ResponseMetadata": {"RequestId": local_request_id}})
    result, _, _ = system(tmp_path)
    original = agent_for(result, "Analyst")
    output = HarnessAgent(original.configuration, original.deployment, client).invoke(INPUT, context=context())

    assert output == HarnessFailed("transport_failure", local_request_id, None, True)
    event = _failure_event(caplog)
    assert event["request_id"] == (fallback_id if local_request_id == "unsafe-local-id" else local_request_id)
    assert "private-provider-marker" not in caplog.text and "unsafe-local-id" not in caplog.text
    assert events.close_count == 1 and len(client.requests) == 1


def test_client_setup_failure_is_logged_without_a_request_or_retry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.ERROR, logger=harness_invocation.__name__)
    result, client, _ = system(tmp_path)

    def unavailable(service: str, *, region: str, read_timeout: int) -> object:
        raise ConnectionError("private-setup-marker")

    monkeypatch.setattr(harness_invocation, "aws_client", unavailable)
    handle = replace(agent_for(result, "Analyst"), client=None)
    assert handle.invoke(INPUT, context=context()) == HarnessFailed("transport_failure", None, None, False)
    event = _failure_event(caplog)
    assert event["phase"] == "setup" and event["exception_class"] == "ConnectionError"
    assert client.requests == [] and "private-setup-marker" not in caplog.text


def test_import_failure_still_propagates_and_closes_once_without_a_failure_result(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger=harness_invocation.__name__)
    events = _DiagnosticStream([], read_error=ImportError("private-import-marker"))
    client = _DiagnosticClient({"stream": events})
    result, _, _ = system(tmp_path)
    original = agent_for(result, "Analyst")
    handle = HarnessAgent(original.configuration, original.deployment, client)

    with pytest.raises(ImportError):
        handle.invoke(INPUT, context=context())
    assert events.close_count == 1 and len(client.requests) == 1
    assert caplog.records == []


@pytest.mark.parametrize("text", [json.dumps(OUTPUT), "invalid-output-marker"])
def test_completed_stream_does_not_emit_an_exception_diagnostic(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, text: str,
) -> None:
    caplog.set_level(logging.ERROR, logger=harness_invocation.__name__)
    events = _DiagnosticStream(success_stream(text))
    client = _DiagnosticClient({"stream": events})
    result, _, _ = system(tmp_path)
    original = agent_for(result, "Analyst")
    output = HarnessAgent(original.configuration, original.deployment, client).invoke(INPUT, context=context())

    if text == json.dumps(OUTPUT):
        assert isinstance(output, HarnessCompleted)
    else:
        assert output == HarnessFailed("invalid_output", None, "end_turn", True)
    assert events.close_count == 1 and len(client.requests) == 1
    assert caplog.records == []


def test_exact_approval_result_and_recovery_binding(tmp_path: Path) -> None:
    result, client, sink = system(tmp_path, approval=True)
    handle = agent_for(result, "Analyst")
    tool_name = next(iter(handle.configuration.tools))
    client.streams.extend([tool_stream(tool_name, INPUT), success_stream(json.dumps(OUTPUT))])
    interrupted = handle.invoke(INPUT, context=context())
    assert isinstance(interrupted, HarnessInterrupted)
    pending = HarnessPendingCall.from_dict(interrupted.pending.to_dict())
    assert pending == interrupted.pending
    assert handle.tool_arguments(pending, context=context()) == INPUT
    with pytest.raises(ValueError, match="approval"):
        handle.resume(pending, OUTPUT, context=context())
    with pytest.raises(ValueError, match="exact"):
        handle.resume(pending, OUTPUT, context=context(), approval=HarnessApproval("wrong", True, "approval:1"))
    for changed in (
        replace(pending, tool_use_id="wrong"),
        replace(pending, arguments=frozen_object({})),
        replace(pending, configuration_digest="wrong"),
    ):
        with pytest.raises(ValueError):
            handle.resume(changed, OUTPUT, context=context())
    with pytest.raises(ValueError, match="identity"):
        handle.resume(pending, OUTPUT, context=context(principal_id="other"))
    approval = HarnessApproval(pending.call_digest, True, "approval:1")
    with pytest.raises(ValueError, match="Record"):
        handle.resume(pending, OUTPUT, context=context(), approval=approval)
    handle.record_approval(pending, approval, context=context())
    with pytest.raises(ValidationError):
        handle.resume(
            pending, {"answer": 7}, context=context(), approval=HarnessApproval(pending.call_digest, True, "approval:1")
        )
    recovered = HarnessAgent(handle.configuration, handle.deployment, client, sink)
    recovered.restore_pending(pending, context=context(), approval=approval)
    output = recovered.resume(pending, OUTPUT, context=context(), approval=approval)
    assert isinstance(output, HarnessCompleted)
    submitted = json_object("toolResult", request_content(client, -1)["toolResult"])
    assert submitted["toolUseId"] == pending.tool_use_id
    text = json_object("tool content", json_array("content", submitted["content"])[0])["text"]
    assert isinstance(text, str) and json.loads(text) == OUTPUT
    with pytest.raises(ValueError, match="already"):
        recovered.resume(pending, OUTPUT, context=context(), approval=approval)
    with pytest.raises(ValueError, match="already"):
        recovered.restore_pending(pending, context=context())
    NormalizedTrace(tuple(sink.events))


def test_denied_approval_resumes_with_error_without_accepting_a_result(tmp_path: Path) -> None:
    result, client, sink = system(tmp_path, approval=True)
    handle = agent_for(result, "Analyst")
    client.streams.extend([tool_stream(next(iter(handle.configuration.tools)), INPUT), success_stream()])
    pending = pending_call(handle.invoke(INPUT, context=context()))
    decision = HarnessApproval(pending.call_digest, False, "approval:denied")
    handle.record_approval(pending, decision, context=context())
    handle.resume(pending, object(), context=context(), approval=decision)
    submitted = json_object("toolResult", request_content(client, -1)["toolResult"])
    assert submitted["status"] == "error"
    assert "tool.completed" not in {event.event_type for event in sink.events}


def test_recorded_approval_correlates_with_host_execution_in_the_shared_assessor(tmp_path: Path) -> None:
    import time

    from contract4agents.assurance import assess_controls

    result, client, sink = system(tmp_path, approval=True)
    handle = agent_for(result, "Analyst")
    client.streams.extend([tool_stream(next(iter(handle.configuration.tools)), INPUT), success_stream()])
    pending = pending_call(handle.invoke(INPUT, context=context()))
    approval = HarnessApproval(pending.call_digest, True, "host:decision")
    handle.record_approval(pending, approval, context=context())
    handle.record_approval(pending, approval, context=context())
    decision_events = [event for event in sink.events if event.event_type == "approval.completed"]
    assert len(decision_events) == 1
    approved = decision_events[0]
    assert approved.data["approved"] is True
    assert approved.provider.span_id == pending.call_digest
    tool = handle.configuration.tools[pending.tool_name]
    sink.emit(replace(approved, event_type="tool.started", event_id="host:tool:start", timestamp=time.time()))
    implementation = result.graph.implementations[tool.semantic_id]
    assert callable(implementation)
    value = implementation(**handle.tool_arguments(pending, context=context()))
    sink.emit(replace(approved, event_type="tool.completed", event_id="host:tool:end", timestamp=time.time()))
    handle.resume(pending, value, context=context(), approval=approval)
    assessed = assess_controls(result, NormalizedTrace(tuple(sink.events)))
    assert next(item for item in assessed if "approval" in str(item.control_id)).status == "passed"
    assert next(item for item in assessed if "output_conformance" in str(item.control_id)).status == "unverified"


def test_ungranted_or_invalid_tool_input_never_returns_a_pending_action(tmp_path: Path) -> None:
    result, client, _ = system(tmp_path)
    handle = agent_for(result, "Analyst")
    client.streams.extend([tool_stream("shell", {}), tool_stream(next(iter(handle.configuration.tools)), {})])
    assert failure(handle.invoke(INPUT, context=context())).reason == "ungranted_tool"
    assert (
        failure(handle.invoke(INPUT, context=context(session_id="00000000-0000-0000-0000-000000000002"))).reason
        == "invalid_tool_input"
    )


def test_bounded_delegation_preserves_inputs_child_identity_and_parent_trace(tmp_path: Path) -> None:
    result, client, sink = system(tmp_path, delegate=True)
    parent, child = agent_for(result, "Parent"), agent_for(result, "Analyst")
    name = next(iter(parent.configuration.tools))
    client.streams.extend(
        [tool_stream(name, INPUT), success_stream(json.dumps(OUTPUT)), success_stream(json.dumps(OUTPUT))]
    )
    pending = pending_call(parent.invoke(INPUT, context=context()))
    child_context = context(
        session_id="00000000-0000-0000-0000-000000000002",
        attempt=TraceAttempt("child-invocation", "child-attempt", 1),
        parent_event_id=pending.event_id,
    )
    with pytest.raises(ValueError, match="child"):
        parent.invoke_delegate(pending, parent, context=context(), child_context=child_context)
    with pytest.raises(ValueError, match="scope"):
        parent.invoke_delegate(pending, child, context=context(), child_context=context())
    output = parent.invoke_delegate(pending, child, context=context(), child_context=child_context)
    assert isinstance(output, HarnessCompleted)
    final = parent.resume(pending, output.value, context=context())
    assert isinstance(final, HarnessCompleted)
    assert client.requests[1]["messages"] == [
        {"role": "user", "content": [{"text": json.dumps(INPUT, sort_keys=True, separators=(",", ":"))}]}
    ]
    NormalizedTrace(tuple(sink.events))


def test_delegation_rejects_model_changes_to_mapped_input(tmp_path: Path) -> None:
    result, client, _ = system(tmp_path, delegate=True)
    handle = agent_for(result, "Parent")
    client.streams.append(tool_stream(next(iter(handle.configuration.tools)), {"request": {"question": "changed"}}))
    assert failure(handle.invoke(INPUT, context=context())).reason == "invalid_tool_input"


@pytest.mark.parametrize(
    ("fields", "source", "arguments", "expected"),
    [
        ('    locale: string = "en"', {"question": "q"}, {"question": "q"}, {"question": "q", "locale": "en"}),
        (
            "    when: datetime",
            {"question": "q", "when": datetime(2026, 10, 24, 12, tzinfo=UTC)},
            {"question": "q", "when": "2026-10-24T12:00:00+00:00"},
            {"question": "q", "when": "2026-10-24T12:00:00Z"},
        ),
    ],
)
def test_delegation_compares_normalized_typed_input_through_child_invocation(
    tmp_path: Path, fields: str, source: dict[str, object], arguments: dict[str, object], expected: dict[str, object]
) -> None:
    result, client, _ = system(tmp_path, delegate=True, request_fields=fields)
    parent, child = agent_for(result, "Parent"), agent_for(result, "Analyst")
    client.streams.extend(
        [
            tool_stream(next(iter(parent.configuration.tools)), {"request": arguments}),
            success_stream(json.dumps(OUTPUT)),
            success_stream(json.dumps(OUTPUT)),
        ]
    )
    interrupted = parent.invoke({"request": source}, context=context())
    assert isinstance(interrupted, HarnessInterrupted)
    child_context = context(
        session_id="00000000-0000-0000-0000-000000000002",
        attempt=TraceAttempt("child-invocation", "child-attempt", 1),
        parent_event_id=interrupted.pending.event_id,
    )
    completed = parent.invoke_delegate(interrupted.pending, child, context=context(), child_context=child_context)
    assert isinstance(completed, HarnessCompleted)
    text = request_content(client, 1)["text"]
    assert isinstance(text, str)
    assert json.loads(text) == {"request": expected}
    assert isinstance(parent.resume(interrupted.pending, completed.value, context=context()), HarnessCompleted)


def test_delegation_still_rejects_a_changed_value_when_defaults_are_present(tmp_path: Path) -> None:
    result, client, _ = system(tmp_path, delegate=True, request_fields='    locale: string = "en"')
    parent = agent_for(result, "Parent")
    client.streams.append(
        tool_stream(
            next(iter(parent.configuration.tools)), {"request": {"question": "synthetic question", "locale": "fr"}}
        )
    )
    outcome = parent.invoke(INPUT, context=context())
    assert isinstance(outcome, HarnessFailed) and outcome.reason == "invalid_tool_input"
    assert len(client.requests) == 1


def test_usage_is_scoped_to_each_response_and_missing_counts_stay_unavailable(tmp_path: Path) -> None:
    usage = {"inputTokens": 2, "outputTokens": 3, "totalTokens": 5}
    result, client, sink = system(tmp_path)
    handle = agent_for(result, "Analyst")
    client.streams.extend(
        [
            tool_stream(next(iter(handle.configuration.tools)), INPUT, metadata=usage),
            success_stream(json.dumps(OUTPUT), metadata=usage),
        ]
    )
    pending = pending_call(handle.invoke(INPUT, context=context()))
    handle.resume(pending, OUTPUT, context=context())
    records = [
        json_object("evidence", e.data["evidence"])
        for e in sink.events if e.event_type == "provider.usage.reported"
    ]
    assert len(records) == 2
    assert len({fact["aggregation_identity"] for fact in records}) == 2
    assert all(fact["request_count"] is None and fact["coverage"] == "partial" for fact in records)


def test_run_context_and_persisted_state_reject_ambiguous_shapes() -> None:
    with pytest.raises(ValueError):
        context(session_id="short")
    with pytest.raises(ValueError):
        context(principal_id="")
    with pytest.raises(TypeError):
        context(attempt=None)  # type: ignore[arg-type]
    assert HarnessRunContext.from_dict(context().to_dict()) == context()
    with pytest.raises(ValueError):
        HarnessRunContext.from_dict({})
    with pytest.raises(ValueError):
        HarnessPendingCall.from_dict({})
    with pytest.raises(ValueError):
        HarnessApproval("call", True, "")
