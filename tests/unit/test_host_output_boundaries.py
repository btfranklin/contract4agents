from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from pydantic import BaseModel, ConfigDict, RootModel, StrictStr, TypeAdapter, create_model

from contract4agents import materialize
from contract4agents.materialization import AgentsSDK, OpenAIMaterializationProvider
from contract4agents.materialization._google_adk import ADKSDK
from contract4agents.materialization._strands import StrandsAgentsSDK
from tests.unit.support.openai import FakeOpenAISDK, _write_project

OutputShape = Literal["outer_model", "nested_model", "root_model"]


class ApplicationItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: str


class ApplicationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ApplicationItem]


class ApplicationItems(RootModel[list[ApplicationItem]]):
    model_config = ConfigDict(strict=True)


class ContractItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: StrictStr


class ContractEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ContractItem]


class ApplicationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    active: bool
    checked_at: datetime


class ContractStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    active: bool
    checked_at: datetime


INPUT_TYPE = create_model(
    "HostOutputInput",
    __config__=ConfigDict(extra="forbid", strict=True),
    query=(StrictStr, ...),
)
EXPECTED_ITEMS = {"items": [{"label": "needle"}]}


def _tool_case(shape: OutputShape, *, asynchronous: bool) -> tuple[object, TypeAdapter[Any]]:
    def result(query: str) -> object:
        item = ApplicationItem(label=query)
        if shape == "outer_model":
            return ApplicationEnvelope(items=[item])
        if shape == "nested_model":
            return {"items": [item]}
        return ApplicationItems([item])

    if asynchronous:

        async def implementation(query: str) -> object:
            return result(query)

    else:

        def implementation(query: str) -> object:
            return result(query)

    adapter = TypeAdapter(ContractEnvelope if shape != "root_model" else list[ContractItem])
    return implementation, adapter


def _dump(adapter: TypeAdapter[Any], value: object) -> object:
    return adapter.dump_python(value, mode="json")


@pytest.mark.parametrize("asynchronous", (False, True), ids=("sync", "async"))
@pytest.mark.asyncio
async def test_openai_function_tool_accepts_nested_application_models(
    asynchronous: bool,
) -> None:
    implementation, output_adapter = _tool_case("nested_model", asynchronous=asynchronous)
    tool = AgentsSDK().create_function_tool(
        name="records.lookup",
        description="Look up records.",
        implementation=implementation,
        input_type=INPUT_TYPE,
        output_adapter=output_adapter,
        requires_approval=False,
    )

    output = await cast(Any, tool).on_invoke_tool(SimpleNamespace(), '{"query":"needle"}')

    assert _dump(output_adapter, output) == EXPECTED_ITEMS


@pytest.mark.parametrize("asynchronous", (False, True), ids=("sync", "async"))
@pytest.mark.parametrize("shape", ("outer_model", "nested_model", "root_model"))
@pytest.mark.asyncio
async def test_google_adk_function_tool_accepts_structural_application_models(
    shape: OutputShape,
    asynchronous: bool,
) -> None:
    implementation, output_adapter = _tool_case(shape, asynchronous=asynchronous)
    tool = ADKSDK().create_function_tool(
        native_name="c4a_tool_records_lookup_deadbeef",
        description="Look up records.",
        implementation=implementation,
        input_type=INPUT_TYPE,
        output_adapter=output_adapter,
        requires_approval=False,
    )

    output = await cast(Any, tool).run_async(
        args={"query": "needle"},
        tool_context=SimpleNamespace(),
    )

    expected = EXPECTED_ITEMS if shape != "root_model" else EXPECTED_ITEMS["items"]
    assert output == expected


@pytest.mark.parametrize("asynchronous", (False, True), ids=("sync", "async"))
@pytest.mark.parametrize("shape", ("outer_model", "nested_model", "root_model"))
@pytest.mark.asyncio
async def test_strands_function_tool_accepts_structural_application_models(
    shape: OutputShape,
    asynchronous: bool,
) -> None:
    implementation, output_adapter = _tool_case(shape, asynchronous=asynchronous)
    tool = StrandsAgentsSDK().create_function_tool(
        native_name="c4a_tool_records_lookup_deadbeef",
        description="Look up records.",
        implementation=implementation,
        input_type=INPUT_TYPE,
        output_adapter=output_adapter,
    )

    events = [
        event
        async for event in cast(Any, tool).stream(
            {
                "name": "c4a_tool_records_lookup_deadbeef",
                "toolUseId": "tool-use-1",
                "input": {"query": "needle"},
            },
            {},
        )
    ]

    expected = EXPECTED_ITEMS if shape != "root_model" else EXPECTED_ITEMS["items"]
    assert events[-1]["tool_result"] == {
        "toolUseId": "tool-use-1",
        "status": "success",
        "content": [{"json": expected}],
    }


@pytest.mark.parametrize("asynchronous", (False, True), ids=("sync", "async"))
@pytest.mark.asyncio
async def test_google_adk_function_tool_treats_string_output_as_a_python_value(
    asynchronous: bool,
) -> None:
    if asynchronous:

        async def implementation(query: str) -> str:
            return query

    else:

        def implementation(query: str) -> str:
            return query

    tool = ADKSDK().create_function_tool(
        native_name="c4a_tool_string_lookup_deadbeef",
        description="Return one string.",
        implementation=implementation,
        input_type=INPUT_TYPE,
        output_adapter=TypeAdapter(StrictStr),
        requires_approval=False,
    )

    output = await cast(Any, tool).run_async(
        args={"query": "needle"},
        tool_context=SimpleNamespace(),
    )

    assert output == "needle"


@pytest.mark.parametrize("callable_shape", ("async_callable", "returns_awaitable"))
@pytest.mark.asyncio
async def test_openai_function_tool_awaits_all_supported_callable_shapes(
    callable_shape: str,
) -> None:
    async def finish(query: str) -> dict[str, list[dict[str, str]]]:
        return {"items": [{"label": query}]}

    if callable_shape == "async_callable":

        class AsyncCallable:
            async def __call__(self, query: str) -> dict[str, list[dict[str, str]]]:
                return await finish(query)

        implementation: object = AsyncCallable()
    else:

        def returns_awaitable(query: str) -> object:
            return finish(query)

        implementation = returns_awaitable

    output_adapter = TypeAdapter(ContractEnvelope)
    tool = AgentsSDK().create_function_tool(
        name="records.lookup",
        description="Look up records.",
        implementation=implementation,
        input_type=INPUT_TYPE,
        output_adapter=output_adapter,
        requires_approval=False,
    )

    output = await cast(Any, tool).on_invoke_tool(SimpleNamespace(), '{"query":"needle"}')

    assert _dump(output_adapter, output) == EXPECTED_ITEMS


@pytest.mark.parametrize("asynchronous", (False, True), ids=("sync", "async"))
@pytest.mark.parametrize("boundary", ("datasource", "external"))
@pytest.mark.asyncio
async def test_context_runtime_accepts_application_pydantic_results(
    tmp_path: Path,
    boundary: str,
    asynchronous: bool,
) -> None:
    _write_project(tmp_path)
    current_async = "async " if asynchronous and boundary == "datasource" else ""
    context_async = "async " if asynchronous and boundary == "external" else ""
    current_result = "ApplicationResult(value=query)" if boundary == "datasource" else '{"value": query}'
    context_result = "ApplicationResult(value=\"context\")" if boundary == "external" else '{"value": "context"}'
    (tmp_path / "app_impl.py").write_text(
        f"""\
from pydantic import BaseModel, ConfigDict

class ApplicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str

def lookup(query: str):
    return {{"value": query}}

{current_async}def current(query: str):
    return {current_result}

{context_async}def context():
    return {context_result}
"""
    )
    system = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    resolved = await system.context.resolve_agent(
        "Child",
        {"request": {"value": "needle"}},
        run_id="run-1",
    )

    assert cast(Any, resolved["current"].value).value == "needle"
    assert cast(Any, resolved["metadata"].value).value == "context"


@pytest.mark.parametrize("callable_shape", ("function", "callable"))
@pytest.mark.parametrize("boundary", ("datasource", "external"))
@pytest.mark.asyncio
async def test_context_runtime_runs_synchronous_providers_off_the_event_loop(
    tmp_path: Path,
    boundary: str,
    callable_shape: str,
) -> None:
    _write_project(tmp_path)
    selected_name = "current" if boundary == "datasource" else "context"
    parameter = "query: str" if boundary == "datasource" else ""
    if callable_shape == "function":
        selected_definition = f"""\
def {selected_name}({parameter}):
    return {{"value": str(threading.get_ident())}}
"""
    else:
        selected_definition = f"""\
class SelectedProvider:
    def __call__(self, {parameter}):
        return {{"value": str(threading.get_ident())}}

{selected_name} = SelectedProvider()
"""
    current_definition = (
        selected_definition
        if boundary == "datasource"
        else 'def current(query: str):\n    return {"value": query}\n'
    )
    context_definition = (
        selected_definition
        if boundary == "external"
        else 'def context():\n    return {"value": "context"}\n'
    )
    (tmp_path / "app_impl.py").write_text(
        'import threading\n\ndef lookup(query: str):\n    return {"value": query}\n\n'
        + current_definition
        + "\n"
        + context_definition
    )
    event_loop_thread = str(threading.get_ident())
    system = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    resolved = await system.context.resolve_agent(
        "Child",
        {"request": {"value": "needle"}},
        run_id="run-1",
    )

    selected_context = "current" if boundary == "datasource" else "metadata"
    assert cast(Any, resolved[selected_context].value).value != event_loop_thread


def _openai_model_visible_case(
    output_shape: str,
) -> tuple[object, TypeAdapter[Any], object]:
    if output_shape == "application_model":
        value = ApplicationEnvelope(items=[ApplicationItem(label="🍩")])
        return value, TypeAdapter(ContractEnvelope), {"items": [{"label": "🍩"}]}
    if output_shape == "nested_mapping":
        value = {"items": [ApplicationItem(label="needle")]}
        return value, TypeAdapter(ContractEnvelope), {"items": [{"label": "needle"}]}
    value = ApplicationStatus(
        active=True,
        checked_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return (
        value,
        TypeAdapter(ContractStatus),
        {"active": True, "checked_at": "2026-01-01T00:00:00Z"},
    )


@pytest.mark.parametrize("asynchronous", (False, True), ids=("sync", "async"))
@pytest.mark.parametrize(
    "output_shape",
    ("application_model", "nested_mapping", "datetime_model"),
)
@pytest.mark.asyncio
async def test_openai_runner_sends_validated_tool_results_to_the_model_as_json(
    output_shape: str,
    asynchronous: bool,
) -> None:
    from agents import Agent, FunctionTool, ModelResponse, RunConfig, Runner, Usage
    from agents.models.interface import Model
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    host_value, output_adapter, expected = _openai_model_visible_case(output_shape)
    if asynchronous:

        async def implementation(query: str) -> object:
            del query
            return host_value

    else:

        def implementation(query: str) -> object:
            del query
            return host_value

    tool = cast(
        FunctionTool,
        AgentsSDK().create_function_tool(
            name="records.lookup",
            description="Look up records.",
            implementation=implementation,
            input_type=INPUT_TYPE,
            output_adapter=output_adapter,
            requires_approval=False,
        ),
    )

    class ToolThenFinalModel(Model):
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
            model_input = kwargs.get("input", args[1] if len(args) > 1 else None)
            self.inputs.append(model_input)
            if len(self.inputs) == 1:
                return ModelResponse(
                    output=[
                        ResponseFunctionToolCall(
                            arguments='{"query":"needle"}',
                            call_id="call-1",
                            name=tool.name,
                            type="function_call",
                        )
                    ],
                    usage=Usage(),
                    response_id=None,
                )
            return ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="message-1",
                        content=[
                            ResponseOutputText(
                                annotations=[],
                                text="done",
                                type="output_text",
                            )
                        ],
                        role="assistant",
                        status="completed",
                        type="message",
                    )
                ],
                usage=Usage(),
                response_id=None,
            )

        async def stream_response(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
            if False:
                yield None

    model = ToolThenFinalModel()
    agent = Agent(
        name="Reader",
        instructions="Read one record.",
        model=model,
        tools=[tool],
    )

    result = await Runner.run(
        agent,
        input="Read one record.",
        max_turns=3,
        run_config=RunConfig(tracing_disabled=True),
    )

    assert result.final_output == "done"
    second_turn = cast(list[dict[str, object]], model.inputs[1])
    tool_output = next(item for item in second_turn if item.get("type") == "function_call_output")
    assert json.loads(cast(str, tool_output["output"])) == expected
