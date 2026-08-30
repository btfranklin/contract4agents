from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError, create_model

from contract4agents import materialize
from contract4agents.ir import (
    FrozenMap,
    freeze_json,
    semantic_id,
)
from contract4agents.materialization import (
    AgentsSDK,
    GraphValidationEvidence,
    MaterializationError,
    SchemaConformanceEvidence,
)
from contract4agents.target_bindings import BindingEntry
from tests.support.openai import write_project


def test_concrete_openai_materializer_builds_real_sdk_objects_without_live_calls(tmp_path: Path) -> None:
    from agents import Agent, FunctionTool, Handoff

    write_project(tmp_path)

    result = materialize(tmp_path, "openai", "test")

    parent = result.agents["Parent"]
    child = result.agents["Child"]
    assert isinstance(parent, Agent)
    assert isinstance(child, Agent)
    assert all(isinstance(item, FunctionTool) for item in parent.tools)
    assert all(isinstance(item, Handoff) for item in parent.handoffs)
    assert cast(FunctionTool, child.tools[0]).needs_approval is True
    assert result.plan.adapter.version != "unavailable"


def test_concrete_openai_materializer_supports_nested_profile_and_agent_options(
    tmp_path: Path,
) -> None:
    from agents import ModelRetrySettings
    from openai.types.shared import Reasoning

    write_project(tmp_path)
    bindings = tmp_path / "contract4agents.targets.toml"
    bindings.write_text(
        bindings.read_text(encoding="utf-8")
        + """\

[targets.openai.profiles.test.options.retry]
max_retries = 0

[targets.openai.profiles.test.agents.Child.options.reasoning]
effort = "high"
""",
        encoding="utf-8",
    )

    result = materialize(tmp_path, "openai", "test")

    parent_settings = cast(Any, result.agents["Parent"]).model_settings
    child_settings = cast(Any, result.agents["Child"]).model_settings
    assert isinstance(parent_settings.retry, ModelRetrySettings)
    assert parent_settings.retry.max_retries == 0
    assert isinstance(child_settings.retry, ModelRetrySettings)
    assert isinstance(child_settings.reasoning, Reasoning)
    assert child_settings.reasoning.effort == "high"
    assert result.graph.validation.complete


def test_concrete_openai_sdk_recursively_thaws_pass_through_model_options() -> None:
    frozen = freeze_json(
        {
            "extra_body": {"custom": {"flags": ["one", "two"]}},
            "tool_choice": {"server_label": "server", "name": "tool"},
        }
    )
    assert isinstance(frozen, FrozenMap)

    agent = AgentsSDK().create_agent(
        name="NestedOptions",
        instructions="Return a result.",
        model="test-model",
        model_options=frozen,
        output_type=create_model("NestedOptionsOutput", value=(str, ...)),
        tools=(),
    )

    settings = cast(Any, agent).model_settings
    assert settings.extra_body == {"custom": {"flags": ["one", "two"]}}
    assert isinstance(settings.extra_body, dict)
    assert isinstance(settings.extra_body["custom"], dict)
    assert isinstance(settings.extra_body["custom"]["flags"], list)
    assert settings.tool_choice.server_label == "server"
    assert settings.tool_choice.name == "tool"


def test_openai_model_option_validation_errors_are_structured() -> None:
    frozen = freeze_json({"retry": {"backoff": {"initial_delay": -0.1}}})
    assert isinstance(frozen, FrozenMap)

    with pytest.raises(MaterializationError) as caught:
        AgentsSDK().create_agent(
            name="InvalidOptions",
            instructions="Return a result.",
            model="test-model",
            model_options=frozen,
            output_type=create_model("InvalidOptionsOutput", value=(str, ...)),
            tools=(),
        )

    assert [issue.code for issue in caught.value.issues] == ["MAT302"]
    assert "Invalid OpenAI model options" in caught.value.issues[0].message


def test_openai_tool_uses_contract_schema_instead_of_callable_annotations(tmp_path: Path) -> None:
    from agents import FunctionTool

    write_project(tmp_path)
    contract_path = tmp_path / "system.contract"
    contract_path.write_text(
        contract_path.read_text().replace(
            "tool records.lookup(query: string)",
            "tool records.lookup(query: string(min_length=1,max_length=4000))",
        )
    )

    result = materialize(tmp_path, "openai", "test")
    tool = cast(FunctionTool, cast(Any, result.graph.agent("Child")).tools[0])
    query_schema = cast(dict[str, object], tool.params_json_schema["properties"])["query"]

    assert query_schema == {"maxLength": 4000, "minLength": 1, "title": "Query", "type": "string"}
    assert tool.params_json_schema["additionalProperties"] is False
    assert result.graph.validation.complete
    round_trip = type(result.graph.validation).from_dict(result.graph.validation.to_dict())
    assert round_trip == result.graph.validation
    evidence_path = tmp_path / "materialization-conformance.json"
    evidence_path.write_text(result.graph.validation.to_json())
    assert GraphValidationEvidence.load(evidence_path) == result.graph.validation
    with pytest.raises(ValueError, match="Invalid graph validation evidence JSON"):
        GraphValidationEvidence.from_json("{")
    inconsistent = json.loads(result.graph.validation.to_json())
    inconsistent["complete"] = not inconsistent["complete"]
    with pytest.raises(ValueError, match="completeness is inconsistent"):
        GraphValidationEvidence.from_dict(inconsistent)
    inconsistent = json.loads(result.graph.validation.to_json())
    inconsistent["schema_conformance"][0]["declared_digest"] = "sha256:incorrect"
    with pytest.raises(ValueError, match="Declared schema digest is inconsistent"):
        GraphValidationEvidence.from_dict(inconsistent)
    inconsistent = json.loads(result.graph.validation.to_json())
    inconsistent["schema_conformance"][0]["materialized_digest"] = "sha256:incorrect"
    with pytest.raises(ValueError, match="Materialized schema digest is inconsistent"):
        GraphValidationEvidence.from_dict(inconsistent)
    with pytest.raises(TypeError, match="must be an object"):
        GraphValidationEvidence.from_dict([])
    with pytest.raises(ValueError, match="boundary cannot be empty"):
        SchemaConformanceEvidence(semantic_id("agent", "Worker"), "", {}, {})
    with pytest.raises(TypeError, match="requires object schemas"):
        SchemaConformanceEvidence(semantic_id("agent", "Worker"), "agent_output", [], {})
    validated = asyncio.run(tool.on_invoke_tool(SimpleNamespace(), '{"query":"record-1"}'))
    assert validated.value == "record-1"
    with pytest.raises(ValidationError):
        asyncio.run(tool.on_invoke_tool(SimpleNamespace(), '{"query":""}'))


@pytest.mark.parametrize("implementation_kind", ("sync", "async"))
@pytest.mark.asyncio
async def test_openai_materialized_tool_accepts_application_pydantic_output_and_continues_run(
    tmp_path: Path,
    implementation_kind: str,
) -> None:
    from collections.abc import AsyncIterator

    from agents import FunctionTool, ModelResponse, RunConfig, Runner, Usage
    from agents.models.interface import Model
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    write_project(tmp_path)
    contract_path = tmp_path / "system.contract"
    contract_path.write_text(
        contract_path.read_text().replace("authorization = approval_required", "authorization = preapproved")
    )
    async_prefix = "async " if implementation_kind == "async" else ""
    (tmp_path / "app_impl.py").write_text(
        f"""\
from pydantic import BaseModel, ConfigDict

class SourceReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str

{async_prefix}def lookup(query: str) -> SourceReadResult:
    return SourceReadResult(value=query)

def current(query: str):
    return {{"value": query}}

def context():
    return {{"value": "context"}}
"""
    )

    system = materialize(tmp_path, "openai", "test")
    child = cast(Any, system.agents["Child"])
    tool = cast(FunctionTool, child.tools[0])

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
                            arguments='{"query":"record-1"}',
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
                                text='{"value":"complete"}',
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

    scripted_model = ToolThenFinalModel()
    child.model = scripted_model
    run = await Runner.run(
        child,
        input="Read one source.",
        max_turns=3,
        run_config=RunConfig(tracing_disabled=True),
    )

    assert type(run.final_output) is system.structural_output_types["Result"]
    assert run.final_output.value == "complete"
    assert len(scripted_model.inputs) == 2
    second_turn = cast(list[dict[str, object]], scripted_model.inputs[1])
    tool_output = next(item for item in second_turn if item.get("type") == "function_call_output")
    assert tool_output["call_id"] == "call-1"
    assert "record-1" in cast(str, tool_output["output"])


@pytest.mark.parametrize(
    ("model_body", "constructor", "error_type"),
    (
        ("    pass", "SourceReadResult()", "missing"),
        (
            "    value: str\n    unexpected: str",
            'SourceReadResult(value=query, unexpected="extra")',
            "extra_forbidden",
        ),
        ("    value: int", "SourceReadResult(value=1)", "string_type"),
    ),
)
@pytest.mark.asyncio
async def test_openai_materialized_tool_keeps_strict_validation_after_pydantic_normalization(
    tmp_path: Path,
    model_body: str,
    constructor: str,
    error_type: str,
) -> None:
    from agents import FunctionTool

    write_project(tmp_path)
    (tmp_path / "app_impl.py").write_text(
        f"""\
from pydantic import BaseModel, ConfigDict

class SourceReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
{model_body}

def lookup(query: str) -> SourceReadResult:
    return {constructor}

def current(query: str):
    return {{"value": query}}

def context():
    return {{"value": "context"}}
"""
    )

    system = materialize(tmp_path, "openai", "test")
    tool = cast(FunctionTool, cast(Any, system.agents["Child"]).tools[0])

    with pytest.raises(ValidationError) as caught:
        await tool.on_invoke_tool(SimpleNamespace(), '{"query":"record-1"}')

    assert error_type in {cast(str, item["type"]) for item in caught.value.errors()}


def test_openai_hosted_tool_option_errors_are_structured() -> None:
    with pytest.raises(MaterializationError) as caught:
        AgentsSDK().create_hosted_tool(
            name="web.search",
            binding=BindingEntry(
                {
                    "provider": "openai",
                    "tool": "web_search",
                    "not_a_web_search_option": True,
                }
            ),
        )

    assert [issue.code for issue in caught.value.issues] == ["MAT304"]
    assert "Invalid OpenAI web-search options" in caught.value.issues[0].message
