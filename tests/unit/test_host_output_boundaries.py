from __future__ import annotations

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
