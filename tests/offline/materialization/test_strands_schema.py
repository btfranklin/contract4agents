"""Native schema output through the real Strands loop and a local HTTP transport."""
from __future__ import annotations

import importlib
import json
import sys
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from types import SimpleNamespace

import httpx2 as httpx
import openai
import pytest
from pydantic import BaseModel, JsonValue
from strands import Agent
from strands.types.content import Messages
from strands.types.tools import (
    ToolChoiceAny,
    ToolChoiceAnyDict,
    ToolChoiceAuto,
    ToolChoiceAutoDict,
    ToolChoiceToolDict,
    ToolSpec,
)

from contract4agents import materialize, plan_project
from contract4agents.adapters.strands import StrandsOutputContext, StrandsOutputMode, extract_strands_output
from contract4agents.adapters.strands_openai import ReasoningHistoryMode, ReasoningOpenAIModel
from contract4agents.materialization import MaterializationError
from contract4agents.tracing import StrandsNormalizedTraceRouter, TraceAttempt


class Observer:
    def __init__(self) -> None:
        self.requests: list[dict[str, JsonValue]] = []
        self.events: list[dict[str, JsonValue]] = []
        self.errors: list[BaseException] = []
        self.completions = 0

    def request(self, payload: dict[str, JsonValue]) -> None:
        self.requests.append(payload)

    def event(self, payload: dict[str, JsonValue]) -> None:
        self.events.append(payload)

    def failed(self, error: BaseException) -> None:
        self.errors.append(error)

    def completed(self) -> None:
        self.completions += 1


def project(root: Path, monkeypatch: pytest.MonkeyPatch) -> object:
    sys.modules.pop("schema_app", None)
    monkeypatch.syspath_prepend(str(root))
    (root / "system.contract").write_text('''type Result:
    value: string(min_length=1,max_length=8)

tool sources.read(source_id: string) -> Result:
    description = "Read one admitted source."
    side_effect = false

agent Reader(request: string) -> Result:
    use sources.read:
        availability = enabled
        authorization = preapproved
        execution = host
    goal = "Use admitted sources for the result."
''')
    (root / "schema_app.py").write_text('''def read(source_id: str) -> dict[str, str]:
    return {"value": source_id}

def make_model(*, model: str, options: object, output: object) -> object:
    raise AssertionError("The test must supply the native model factory")
''')
    (root / "contract4agents.targets.toml").write_text('''schema_version = "1"
[targets.strands]
adapter = "strands"
[targets.strands.tools."sources.read"]
python = "schema_app:read"
[targets.strands.profiles.test]
default_model = "synthetic"
[targets.strands.profiles.test.options]
output_mode = "schema"
model_factory = "schema_app:make_model"
''')
    return importlib.import_module("schema_app")


def sse(delta: dict[str, object], finish: str | None = None) -> bytes:
    return ("data: " + json.dumps({
        "id": "synthetic-response", "created": 1, "model": "synthetic", "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }) + "\n\n").encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("final", ['{"value":"OK"}', '{"value":"TOO-LONG-VALUE"}'])
@pytest.mark.parametrize("reasoning", ["REASONING-MARKER", ""])
async def test_native_schema_runs_source_tool_without_result_tool_and_validates_full_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, final: str, reasoning: str,
) -> None:
    module = project(tmp_path, monkeypatch)
    observer = Observer()
    wire: list[dict[str, object]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        payload: dict[str, object] = json.loads(request.content)
        wire.append(payload)
        if len(wire) == 1:
            tools = payload["tools"]
            assert isinstance(tools, list) and len(tools) == 1
            function = tools[0]["function"]
            body = sse({"role": "assistant", "reasoning_content": reasoning}) + sse({"tool_calls": [{
                "index": 0, "id": "source-call", "type": "function",
                "function": {"name": function["name"], "arguments": '{"source_id":"SOURCE"}'},
            }]}) + sse({}, "tool_calls")
        else:
            body = sse({"role": "assistant", "content": final}) + sse({}, "stop")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body + b"data: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http_client:
        async with openai.AsyncOpenAI(api_key="synthetic", base_url="https://provider.invalid", max_retries=0,
                                      http_client=http_client) as client:
            contexts: list[StrandsOutputContext] = []
            def make_model(*, model: str, options: Mapping[str, object], output: StrandsOutputContext) -> object:
                assert options == {}
                contexts.append(output)
                return ReasoningOpenAIModel(client=client, model_id=model, params={
                    "response_format": output.chat_completions_format(),
                }).with_reasoning_history(ReasoningHistoryMode.REQUIRED).observe(observer)
            monkeypatch.setattr(module, "make_model", make_model)
            system = materialize(tmp_path, target="strands", profile="test")
            agent = system.agents["Reader"]
            assert isinstance(agent, Agent)
            router = StrandsNormalizedTraceRouter()
            router.attach(system)
            session = router.open_session(system, run_id="schema-test")
            with session, session.bind_attempt(TraceAttempt("schema:1", "schema:invoke", 1), agent=agent):
                result = await agent.invoke_async("Read the admitted source.")
            assert result.structured_output is None
            assert not client.is_closed() and not http_client.is_closed
            if "TOO-LONG" in final:
                with pytest.raises(MaterializationError):
                    extract_strands_output(system, agent, result)
                assert "output.schema_failed" in {event.event_type for event in session.closed_snapshot.trace.events}
            else:
                value = extract_strands_output(system, agent, result)
                assert isinstance(value, BaseModel) and value.model_dump() == {"value": "OK"}
                assert "output.accepted" in {event.event_type for event in session.closed_snapshot.trace.events}
            assert len(wire) == 2 and len(contexts) == 1
            assert observer.requests == wire
            assert observer.completions == 2 and observer.errors == []
            assert observer.events
            messages = wire[1]["messages"]
            assert isinstance(messages, list)
            assistant = next(row for row in messages if row["role"] == "assistant")
            assert assistant["reasoning_content"] == reasoning
            specification = contexts[0].chat_completions_format()["json_schema"]
            assert isinstance(specification, dict) and specification["strict"] is True
            assert '"maxLength"' not in json.dumps(specification)
            assert '"maxLength"' in json.dumps(contexts[0].schema)
        assert client.is_closed() and http_client.is_closed


@pytest.mark.parametrize("reasoning", ["THINK", ""])
def test_reasoning_serialization_preserves_tool_call_identity_and_input(reasoning: str) -> None:
    messages: Messages = [
        {"role": "user", "content": [{"text": "INPUT"}]},
        {"role": "assistant", "content": [
            {"reasoningContent": {"reasoningText": {"text": reasoning}}},
            {"toolUse": {"toolUseId": "read-1", "name": "read", "input": {"source_id": "SOURCE"}}},
        ]},
        {"role": "user", "content": [{"toolResult": {
            "toolUseId": "read-1", "status": "success", "content": [{"text": "SOURCE"}],
        }}]},
    ]
    before = json.dumps(messages, sort_keys=True)
    result = ReasoningOpenAIModel.format_request_messages(messages)
    assert result[1]["reasoning_content"] == reasoning
    assert result[1]["tool_calls"] == [{"id": "read-1", "type": "function", "function": {
        "name": "read", "arguments": '{"source_id": "SOURCE"}',
    }}]
    assert result[2] == {"role": "tool", "tool_call_id": "read-1", "content": "SOURCE"}
    assert json.dumps(messages, sort_keys=True) == before


@pytest.mark.parametrize("mode", ["invalid", 2])
def test_invalid_output_mode_refuses_before_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: object,
) -> None:
    project(tmp_path, monkeypatch)
    target = tmp_path / "contract4agents.targets.toml"
    target.write_text(target.read_text().replace('output_mode = "schema"', f'output_mode = {json.dumps(mode)}'))
    with pytest.raises(MaterializationError) as error:
        plan_project(tmp_path, target="strands", profile="test")
    assert "TGT113" in {issue.code for issue in error.value.issues}


def test_schema_factory_cannot_omit_native_generation_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = project(tmp_path, monkeypatch)
    def make_model(*, model: str, options: Mapping[str, object], output: StrandsOutputContext) -> object:
        return ReasoningOpenAIModel(model_id=model)
    monkeypatch.setattr(module, "make_model", make_model)
    with pytest.raises(MaterializationError) as error:
        materialize(tmp_path, target="strands", profile="test")
    assert "MAT319" in {issue.code for issue in error.value.issues}


def test_output_mode_is_enum_like() -> None:
    assert {mode.value for mode in StrandsOutputMode} == {"tool", "schema"}


@pytest.mark.asyncio
async def test_failed_stream_retains_reasoning_partial_output_and_error_without_complete_marker() -> None:
    observer = Observer()
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield sse({"role": "assistant", "reasoning_content": "PARTIAL-REASONING"})
            yield sse({"content": '{"value":'})
            raise httpx.ReadError("INJECTED-STREAM-FAILURE")
    def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=BrokenStream())
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http_client:
        async with openai.AsyncOpenAI(api_key="synthetic", base_url="https://provider.invalid", max_retries=0,
                                      http_client=http_client) as client:
            model = ReasoningOpenAIModel(client=client, model_id="synthetic").observe(observer)
            with pytest.raises(openai.APIConnectionError):
                async for _event in model.stream([{"role": "user", "content": [{"text": "SOURCE"}]}]):
                    pass
    assert len(observer.requests) == 1
    assert observer.completions == 0 and len(observer.errors) == 1
    assert "PARTIAL-REASONING" in json.dumps(observer.events)
    assert {"contentBlockDelta": {"delta": {"text": '{"value":'}}} in observer.events


@pytest.mark.parametrize("body,stop", [
    ('{"value":"OK","value":"BAD"}', "end_turn"),
    ('{"value":NaN}', "end_turn"),
    ('{"value":"OK","extra":true}', "end_turn"),
    ('{"value":"OK"}', "max_tokens"),
    ('{"value":', "end_turn"),
])
def test_native_extraction_refuses_invalid_or_incomplete_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, stop: str,
) -> None:
    module = project(tmp_path, monkeypatch)
    def make_model(*, model: str, options: Mapping[str, object], output: StrandsOutputContext) -> object:
        return ReasoningOpenAIModel(model_id=model, params={"response_format": output.chat_completions_format()})
    monkeypatch.setattr(module, "make_model", make_model)
    system = materialize(tmp_path, target="strands", profile="test")
    result = SimpleNamespace(stop_reason=stop, message={"role": "assistant", "content": [{"text": body}]})
    with pytest.raises((ValueError, MaterializationError)):
        extract_strands_output(system, system.agents["Reader"], result)


def test_inherited_factory_signature_must_support_selected_agent_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project(tmp_path, monkeypatch)
    target = tmp_path / "contract4agents.targets.toml"
    target.write_text(target.read_text() + (
        '\n[targets.strands.profiles.test.agents.Reader.options]\noutput_mode = "tool"\n'
    ))
    with pytest.raises(MaterializationError) as error:
        plan_project(tmp_path, target="strands", profile="test")
    assert "TGT114" in {issue.code for issue in error.value.issues}


def test_required_reasoning_history_is_checked_and_default_does_not_invent_a_field() -> None:
    messages: Messages = [{"role": "assistant", "content": [{"text": "ANSWER"}]}]
    assert "reasoning_content" not in ReasoningOpenAIModel.format_request_messages(messages)[0]
    model = ReasoningOpenAIModel(model_id="synthetic")
    assert model.reasoning_history_mode == ReasoningHistoryMode.PRESERVE
    model.with_reasoning_history(ReasoningHistoryMode.REQUIRED)
    request = model.format_request(messages)
    output = request["messages"]
    assert isinstance(output, list) and output[0]["reasoning_content"] == ""
    with pytest.raises(TypeError):
        model.with_reasoning_history("required")  # type: ignore[arg-type]  # Exercise the public enum boundary.


_TOOL_HISTORY: Messages = [
    {"role": "user", "content": [{"text": "INPUT"}]},
    {"role": "assistant", "content": [
        {"reasoningContent": {"reasoningText": {"text": "THINK"}}},
        {"toolUse": {"toolUseId": "read-1", "name": "read", "input": {"source_id": "SOURCE"}}},
    ]},
    {"role": "user", "content": [{"toolResult": {
        "toolUseId": "read-1", "status": "success", "content": [{"text": "SOURCE"}],
    }}]},
]
_READ_SPEC: ToolSpec = {"name": "read", "description": "Read one source.",
                        "inputSchema": {"json": {"type": "object", "properties": {}}}}
_ANY_TOOL: ToolChoiceAnyDict = {"any": ToolChoiceAny()}
_AUTO_TOOL: ToolChoiceAutoDict = {"auto": ToolChoiceAuto()}
_NAMED_TOOL: ToolChoiceToolDict = {"tool": {"name": "read"}}
_RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {"name": "Result", "strict": True, "schema": {
    "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"],
    "additionalProperties": False,
}}}


@pytest.mark.parametrize("tool_specs", [None, []])
@pytest.mark.parametrize("tool_choice", [None, _AUTO_TOOL])
def test_request_without_tools_omits_tool_fields_and_keeps_history_and_format(
    tool_specs: list[ToolSpec] | None, tool_choice: ToolChoiceAutoDict | None,
) -> None:
    observer = Observer()
    model = ReasoningOpenAIModel(model_id="synthetic", params={"response_format": _RESPONSE_FORMAT}).observe(observer)
    request = model.format_request(_TOOL_HISTORY, tool_specs, tool_choice=tool_choice)
    assert "tools" not in request and "tool_choice" not in request
    assert request["response_format"] == _RESPONSE_FORMAT
    output = request["messages"]
    assert isinstance(output, list)
    assert output[1]["reasoning_content"] == "THINK"
    assert output[1]["tool_calls"][0]["id"] == "read-1"
    assert output[2] == {"role": "tool", "tool_call_id": "read-1", "content": "SOURCE"}
    assert observer.requests == [request]


def test_request_with_tools_keeps_tool_definitions_and_choice() -> None:
    request = ReasoningOpenAIModel(model_id="synthetic").format_request(
        _TOOL_HISTORY, [_READ_SPEC], tool_choice=_ANY_TOOL,
    )
    assert request["tools"] == [{"type": "function", "function": {
        "name": "read", "description": "Read one source.", "parameters": {"type": "object", "properties": {}},
    }}]
    assert request["tool_choice"] == "required"


@pytest.mark.parametrize("tool_choice", [_ANY_TOOL, _NAMED_TOOL])
def test_required_or_named_tool_choice_without_tools_is_refused(
    tool_choice: ToolChoiceAnyDict | ToolChoiceToolDict,
) -> None:
    with pytest.raises(ValueError):
        ReasoningOpenAIModel(model_id="synthetic").format_request(_TOOL_HISTORY, [], tool_choice=tool_choice)


@pytest.mark.parametrize("base_mode,agent_mode", [("schema", "tool"), ("tool", "schema")])
def test_factory_signature_uses_effective_agent_output_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_mode: str, agent_mode: str,
) -> None:
    module = project(tmp_path, monkeypatch)
    def tool_factory(*, model: str, options: object) -> object:
        raise AssertionError("Planning must not call the factory")
    def schema_factory(*, model: str, options: object, output: object) -> object:
        raise AssertionError("Planning must not call the factory")
    monkeypatch.setattr(module, "make_model", schema_factory)
    monkeypatch.setattr(module, "tool_factory", tool_factory, raising=False)
    path = tmp_path / "contract4agents.targets.toml"
    source = path.read_text().replace('output_mode = "schema"', f'output_mode = "{base_mode}"')
    # The overridden agent never uses the base factory signature.
    source += f'\n[targets.strands.profiles.test.agents.Reader.options]\noutput_mode = "{agent_mode}"\n'
    source += 'model_factory = "schema_app:tool_factory"\n' if agent_mode == "tool" else ''
    path.write_text(source)
    plan_project(tmp_path, target="strands", profile="test")


@pytest.mark.parametrize("unconfigured_agent", [False, True])
def test_schema_factory_requirement_uses_each_effective_agent_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unconfigured_agent: bool,
) -> None:
    project(tmp_path, monkeypatch)
    target = tmp_path / "contract4agents.targets.toml"
    target.write_text(target.read_text().replace('model_factory = "schema_app:make_model"\n', '') +
        '\n[targets.strands.profiles.test.agents.Reader.options]\nmodel_factory = "schema_app:make_model"\n')
    if unconfigured_agent:
        contract = tmp_path / "system.contract"
        contract.write_text(contract.read_text() + (
            '\nagent Other(request: string) -> Result:\n    goal = "Return a result."\n'
        ))
        with pytest.raises(MaterializationError) as error:
            plan_project(tmp_path, target="strands", profile="test")
        assert "TGT113" in {issue.code for issue in error.value.issues}
    else:
        plan_project(tmp_path, target="strands", profile="test")
