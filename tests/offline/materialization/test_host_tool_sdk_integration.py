from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated, Any, Literal, cast

import pytest
from pydantic import BaseModel, BeforeValidator, ConfigDict, RootModel, StrictStr, TypeAdapter, create_model

from contract4agents._portable_validation import parse_portable_datetime
from contract4agents.materialization import AgentsSDK
from contract4agents.materialization._google_adk import ADKSDK
from contract4agents.materialization._strands import StrandsAgentsSDK

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
DATETIME_INPUT_TYPE = create_model(
    "HostDatetimeInput",
    __config__=ConfigDict(extra="forbid", strict=True),
    when=(Annotated[datetime, BeforeValidator(parse_portable_datetime)], ...),
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
@pytest.mark.parametrize("shape", ("outer_model", "nested_model", "root_model"))
@pytest.mark.asyncio
async def test_openai_function_tool_accepts_structural_application_models(
    shape: OutputShape,
    asynchronous: bool,
) -> None:
    implementation, output_adapter = _tool_case(shape, asynchronous=asynchronous)
    tool = AgentsSDK().create_function_tool(
        name="records.lookup",
        description="Look up records.",
        implementation=implementation,
        input_type=INPUT_TYPE,
        output_adapter=output_adapter,
        requires_approval=False,
    )

    output = await cast(Any, tool).on_invoke_tool(SimpleNamespace(), '{"query":"needle"}')

    expected = EXPECTED_ITEMS if shape != "root_model" else EXPECTED_ITEMS["items"]
    assert _dump(output_adapter, output) == expected


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


@pytest.mark.parametrize("provider", ("openai", "google_adk", "strands"))
@pytest.mark.asyncio
async def test_function_tool_passes_portable_datetime_to_python_host(
    provider: str,
) -> None:
    received: list[object] = []

    def implementation(when: datetime) -> str:
        received.append(when)
        return "ok"

    output_adapter = TypeAdapter(StrictStr)
    raw_when = "2026-01-01T00:00:00Z"
    if provider == "openai":
        tool = AgentsSDK().create_function_tool(
            name="records.at_time",
            description="Read records at one time.",
            implementation=implementation,
            input_type=DATETIME_INPUT_TYPE,
            output_adapter=output_adapter,
            requires_approval=False,
        )
        await cast(Any, tool).on_invoke_tool(
            SimpleNamespace(),
            json.dumps({"when": raw_when}),
        )
    elif provider == "google_adk":
        tool = ADKSDK().create_function_tool(
            native_name="c4a_tool_records_at_time_deadbeef",
            description="Read records at one time.",
            implementation=implementation,
            input_type=DATETIME_INPUT_TYPE,
            output_adapter=output_adapter,
            requires_approval=False,
        )
        await cast(Any, tool).run_async(
            args={"when": raw_when},
            tool_context=SimpleNamespace(),
        )
    else:
        tool = StrandsAgentsSDK().create_function_tool(
            native_name="c4a_tool_records_at_time_deadbeef",
            description="Read records at one time.",
            implementation=implementation,
            input_type=DATETIME_INPUT_TYPE,
            output_adapter=output_adapter,
        )
        _ = [
            event
            async for event in cast(Any, tool).stream(
                {
                    "name": "c4a_tool_records_at_time_deadbeef",
                    "toolUseId": "tool-use-1",
                    "input": {"when": raw_when},
                },
                {},
            )
        ]

    assert received == [datetime(2026, 1, 1, tzinfo=UTC)]


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
