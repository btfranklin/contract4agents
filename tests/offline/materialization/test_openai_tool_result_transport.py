from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict, StrictStr, TypeAdapter, create_model

from contract4agents.materialization import AgentsSDK


class ApplicationItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: str


class ApplicationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ApplicationItem]


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
