from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, Unpack, cast

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
from contract4agents.materialization._harness_configuration import frozen_object
from contract4agents.materialization._harness_models import HarnessResult
from contract4agents.tracing import NormalizedTrace, RecordingNormalizedTraceSink, TraceAttempt
from tests.support.agentcore_harness import (
    FakeHarnessControlClient,
    FakeHarnessRuntimeClient,
    success_stream,
    tool_stream,
    write_project,
)

INPUT = {"request": {"question": "synthetic question"}}
OUTPUT = {"answer": "synthetic answer", "evidence_ids": ["source-1"]}


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
    tmp_path: Path, error_code: str, expected: str,
) -> None:
    from botocore.exceptions import ClientError  # type: ignore[import-untyped]

    result, _, sink = system(tmp_path)
    original = agent_for(result, "Analyst")

    class Rejected:
        def invoke_harness(self, **kwargs: object) -> Mapping[str, object]:
            raise ClientError({"Error": {"Code": error_code, "Message": "secret provider text"}}, "InvokeHarness")

    handle = HarnessAgent(original.configuration, original.deployment, Rejected(), sink)
    output = handle.invoke(INPUT, context=context())
    assert isinstance(output, HarnessFailed) and output.reason == expected
    facts = [
        json_object("evidence", e.data["evidence"])
        for e in sink.events if e.event_type == "provider.outcome.reported"
    ]
    assert facts[0]["response_received"] is True
    assert "secret provider text" not in json.dumps([e.to_dict() for e in sink.events])


def test_timeout_before_response_does_not_claim_a_provider_response(tmp_path: Path) -> None:
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
