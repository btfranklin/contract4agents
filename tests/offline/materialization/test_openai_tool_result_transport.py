from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ConfigDict, StrictStr, TypeAdapter, create_model

from contract4agents._strict_json import json_array, json_object, json_string
from contract4agents.materialization import AgentsSDK


class ApplicationItem(BaseModel):  # type: ignore[explicit-any]  # Pydantic constructor.
    model_config = ConfigDict(extra="forbid", strict=True)

    label: str


class ApplicationEnvelope(BaseModel):  # type: ignore[explicit-any]  # Pydantic constructor.
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ApplicationItem]


class ContractItem(BaseModel):  # type: ignore[explicit-any]  # Pydantic constructor.
    model_config = ConfigDict(extra="forbid", strict=True)

    label: StrictStr


class ContractEnvelope(BaseModel):  # type: ignore[explicit-any]  # Pydantic constructor.
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ContractItem]


class ApplicationStatus(BaseModel):  # type: ignore[explicit-any]  # Pydantic constructor.
    model_config = ConfigDict(extra="forbid", strict=True)

    active: bool
    checked_at: datetime


class ContractStatus(BaseModel):  # type: ignore[explicit-any]  # Pydantic constructor.
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
) -> tuple[object, TypeAdapter[object], object]:
    value: object
    if output_shape == "application_model":
        value = ApplicationEnvelope(items=[ApplicationItem(label="🍩")])
        return value, TypeAdapter[object](ContractEnvelope), {"items": [{"label": "🍩"}]}
    if output_shape == "nested_mapping":
        value = {"items": [ApplicationItem(label="needle")]}
        return value, TypeAdapter[object](ContractEnvelope), {"items": [{"label": "needle"}]}
    value = ApplicationStatus(
        active=True,
        checked_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return (
        value,
        TypeAdapter[object](ContractStatus),
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
    from agents.agent_output import AgentOutputSchemaBase
    from agents.handoffs import Handoff
    from agents.items import TResponseInputItem, TResponseStreamEvent
    from agents.model_settings import ModelSettings
    from agents.models.interface import Model, ModelTracing
    from agents.tool import Tool
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
    from openai.types.responses.response_prompt_param import ResponsePromptParam

    host_value, output_adapter, expected = _openai_model_visible_case(output_shape)
    if asynchronous:

        async def asynchronous_implementation(query: str) -> object:
            del query
            return host_value

        implementation: Callable[[str], object] = asynchronous_implementation

    else:

        def synchronous_implementation(query: str) -> object:
            del query
            return host_value

        implementation = synchronous_implementation

    tool = AgentsSDK().create_function_tool(
        name="records.lookup",
        description="Look up records.",
        implementation=implementation,
        input_type=INPUT_TYPE,
        output_adapter=output_adapter,
        requires_approval=False,
    )
    assert isinstance(tool, FunctionTool)
    tool_name = tool.name

    class ToolThenFinalModel(Model):
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def get_response(  # type: ignore[explicit-any]  # SDK aliases contain dynamic payload fields.
            self,
            system_instructions: str | None,
            input: str | list[TResponseInputItem],
            model_settings: ModelSettings,
            tools: list[Tool],
            output_schema: AgentOutputSchemaBase | None,
            handoffs: list[Handoff],
            tracing: ModelTracing,
            *,
            previous_response_id: str | None,
            conversation_id: str | None,
            prompt: ResponsePromptParam | None,
        ) -> ModelResponse:
            self.inputs.append(input)
            if len(self.inputs) == 1:
                return ModelResponse(
                    output=[
                        ResponseFunctionToolCall(
                            arguments='{"query":"needle"}',
                            call_id="call-1",
                            name=tool_name,
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

        async def stream_response(  # type: ignore[explicit-any]  # SDK aliases contain dynamic payload fields.
            self,
            system_instructions: str | None,
            input: str | list[TResponseInputItem],
            model_settings: ModelSettings,
            tools: list[Tool],
            output_schema: AgentOutputSchemaBase | None,
            handoffs: list[Handoff],
            tracing: ModelTracing,
            *,
            previous_response_id: str | None,
            conversation_id: str | None,
            prompt: ResponsePromptParam | None,
        ) -> AsyncIterator[TResponseStreamEvent]:
            raise NotImplementedError("This fixture uses non-streamed model calls")
            yield

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
    second_turn = [
        json_object("model input item", item)
        for item in json_array("second model turn", model.inputs[1])
    ]
    tool_output = next(item for item in second_turn if item.get("type") == "function_call_output")
    assert json.loads(json_string("tool output", tool_output["output"])) == expected
