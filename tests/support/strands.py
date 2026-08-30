"""Strands materialization test support."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter

from contract4agents.ir import FrozenMap, SemanticId
from contract4agents.materialization._strands import NativeStrandsAgentDescription, NativeStrandsToolDescription


@dataclass
class FakeStrandsModel:
    identity: str
    options: Mapping[str, object]


@dataclass
class FakeStrandsTool:
    native_name: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    implementation: object | None = None
    child: object | None = None
    environment: object | None = None
    input_type: type[object] | None = None


@dataclass
class FakeStrandsAgent:
    native_name: str
    contract_name: str
    instructions: str
    model_identity: str
    model: object
    output_type: type[object]
    tools: list[object]
    approval_allowed_tools: tuple[str, ...] | None


@dataclass
class FakeStrandsSDK:
    version: str = "fake-strands-1"
    drop_attached_tools: bool = False
    drop_list_bounds: bool = False
    model_factory_calls: list[tuple[str, Mapping[str, object], object | None]] = field(default_factory=list)

    def create_model(
        self,
        *,
        model: str,
        model_options: Mapping[str, object],
        factory: object | None,
    ) -> object:
        self.model_factory_calls.append((model, model_options, factory))
        if factory is None:
            return FakeStrandsModel(model, dict(model_options))
        assert callable(factory)
        return cast(Any, factory)(model=model, options=model_options)

    def create_agent(
        self,
        *,
        native_name: str,
        contract_name: str,
        instructions: str,
        model_identity: str,
        model: object,
        output_type: type[object],
        tools: tuple[object, ...],
        approval_allowed_tools: tuple[str, ...] | None,
    ) -> object:
        return FakeStrandsAgent(
            native_name,
            contract_name,
            instructions,
            model_identity,
            model,
            output_type,
            list(tools),
            approval_allowed_tools,
        )

    def create_function_tool(
        self,
        *,
        native_name: str,
        description: str,
        implementation: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
    ) -> object:
        input_schema = _input_schema(input_type)
        output_schema = output_adapter.json_schema()
        if self.drop_list_bounds:
            input_schema = _without_list_bounds(input_schema)
            output_schema = _without_list_bounds(output_schema)
        return FakeStrandsTool(
            native_name,
            description,
            input_schema,
            output_schema,
            implementation=implementation,
        )

    def create_delegate_tool(
        self,
        *,
        native_name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
    ) -> object:
        input_schema = _input_schema(input_type)
        output_schema = output_adapter.json_schema()
        if self.drop_list_bounds:
            input_schema = _without_list_bounds(input_schema)
            output_schema = _without_list_bounds(output_schema)
        return FakeStrandsTool(
            native_name,
            description,
            input_schema,
            output_schema,
            child=child,
            input_type=input_type,
        )

    def create_isolated_delegate_tool(
        self,
        *,
        native_name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
        output_adapter: TypeAdapter[Any],
        isolation_id: SemanticId,
        requested_dimensions: FrozenMap[str, str],
        declared_capabilities: tuple[str, ...],
        environment: object,
    ) -> object:
        del isolation_id, requested_dimensions, declared_capabilities
        input_schema = _input_schema(input_type)
        output_schema = output_adapter.json_schema()
        if self.drop_list_bounds:
            input_schema = _without_list_bounds(input_schema)
            output_schema = _without_list_bounds(output_schema)
        return FakeStrandsTool(
            native_name,
            description,
            input_schema,
            output_schema,
            child=child,
            environment=environment,
            input_type=input_type,
        )

    def attach(self, agent: object, *, tools: tuple[object, ...]) -> None:
        assert isinstance(agent, FakeStrandsAgent)
        agent.tools = [] if self.drop_attached_tools else list(tools)

    def describe_agent(self, agent: object) -> NativeStrandsAgentDescription:
        assert isinstance(agent, FakeStrandsAgent)
        return NativeStrandsAgentDescription(
            agent.native_name,
            agent.instructions,
            agent.model_identity,
            agent.output_type,
            tuple(item.native_name for item in agent.tools if isinstance(item, FakeStrandsTool)),
            agent.approval_allowed_tools,
        )

    def describe_tool(self, tool: object) -> NativeStrandsToolDescription:
        assert isinstance(tool, FakeStrandsTool)
        return NativeStrandsToolDescription(
            tool.native_name,
            tool.description,
            tool.input_schema,
            tool.output_schema,
        )

    def validate_result(self, agent: object, result: object) -> object:
        description = self.describe_agent(agent)
        value = getattr(result, "structured_output", None)
        if value is None:
            raise ValueError("missing structured output")
        return TypeAdapter(description.output_type).validate_python(value)


def _input_schema(input_type: type[object] | None) -> Mapping[str, object]:
    if input_type is None:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    return cast(dict[str, object], cast(Any, input_type).model_json_schema())


def _without_list_bounds(schema: Mapping[str, object]) -> dict[str, object]:
    result = dict(schema)
    properties = result.get("properties")
    if isinstance(properties, Mapping):
        result["properties"] = {
            name: (
                {key: value for key, value in property_schema.items() if key not in {"minItems", "maxItems"}}
                if isinstance(property_schema, Mapping) and property_schema.get("type") == "array"
                else property_schema
            )
            for name, property_schema in properties.items()
        }
    return result


def write_project(
    root: Path,
    *,
    model_factory: bool = False,
    scripted_models: bool = False,
    async_lookup: bool = False,
    isolation: bool = False,
) -> None:
    sys.modules.pop("app_impl", None)
    isolation_source = ""
    edge_isolation = ""
    environment_binding = ""
    if isolation:
        isolation_source = """\
isolation CleanContext:
    context = explicit_only
    capabilities = declared_only
    state = fresh
    return = final_output_only

"""
        edge_isolation = "    isolation = CleanContext\n"
        environment_binding = """\
[targets.strands.environments.in_process]
provider = "contract4agents.runtime:InProcessEnvironment"

"""
    (root / "system.contract").write_text(
        f"""\
type Request:
    value: string

type Result:
    value: string

tool records.lookup(query: string) -> Result:
    description = "Look up one record."
    side_effect = false

datasource records.current(query: string) -> Result:
    description = "Resolve the current record."
    render = markdown
    cache = run

external_context request_context -> Request:
    description = "Invocation metadata."
    sensitivity = internal
    render = markdown

{isolation_source}agent Child(request: Request) -> Result:
    use records.lookup:
        availability = enabled
        authorization = approval_required
        execution = host
    context current: Result from datasource records.current:
        map query = input.request.value
    context metadata: Request from external request_context
    goal = "Use the declared lookup tool."

agent Parent(request: Request) -> Result:
    goal = "Delegate to the child."

composition ask_child from Parent to Child:
    mode = delegate
    description = "Ask the child for a result."
    history = none
    map request = input.request
{edge_isolation}
"""
    )
    lookup_definition = (
        """\
async def lookup(query):
    LOOKUPS.append(query)
    return {"value": query}
"""
        if async_lookup
        else """\
def lookup(query):
    LOOKUPS.append(query)
    return {"value": query}
"""
    )
    (root / "app_impl.py").write_text(
        f"""\
FACTORY_CALLS = []
LOOKUPS = []

{lookup_definition}

def current(query):
    return {{"value": query}}

def context():
    return {{"value": "context"}}

def make_model(*, model, options):
    FACTORY_CALLS.append((model, dict(options)))
    return {{"model": model, "options": dict(options)}}
"""
    )
    profile_options = ""
    if model_factory:
        profile_options = """\
[targets.strands.profiles.test.options]
model_factory = "app_impl:make_model"
temperature = 0.2
"""
    if scripted_models:
        profile_options = """\
[targets.strands.profiles.test.options]
model_factory = "app_impl:make_model"

[targets.strands.profiles.test.agents.Child.options]
script = "child"

[targets.strands.profiles.test.agents.Parent.options]
script = "parent"
"""
    (root / "contract4agents.targets.toml").write_text(
        f"""\
schema_version = "1"

[targets.strands]
adapter = "strands"

[targets.strands.tools."records.lookup"]
python = "app_impl:lookup"

[targets.strands.datasources."records.current"]
python = "app_impl:current"

[targets.strands.external_context.request_context]
python = "app_impl:context"

[targets.strands.profiles.test]
default_model = "test-model"

{environment_binding}{profile_options}"""
    )


__all__ = ["FakeStrandsAgent", "FakeStrandsModel", "FakeStrandsSDK", "FakeStrandsTool", "write_project"]
