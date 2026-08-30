"""OpenAI materialization test support."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from contract4agents.adapters._openai_names import openai_tool_name
from contract4agents.ir import FrozenMap, SemanticId
from contract4agents.materialization import NativeAgentDescription, NativeToolDescription
from contract4agents.runtime import EnvironmentProvider
from contract4agents.target_bindings import BindingEntry


@dataclass
class FakeTool:
    name: str
    input_schema: Mapping[str, object] = field(default_factory=dict)
    implementation: object | None = None
    requires_approval: bool = False
    environment: EnvironmentProvider | None = None
    isolation_id: SemanticId | None = None
    dimensions: FrozenMap[str, str] = field(default_factory=FrozenMap)
    declared_capabilities: tuple[str, ...] = ()
    input_type: type[object] | None = None


@dataclass
class FakeHandoff:
    name: str
    child: object
    history: str


@dataclass
class FakeAgent:
    name: str
    instructions: str
    model: str
    model_options: Mapping[str, object]
    output_type: type[object]
    tools: list[object]
    handoffs: list[object] = field(default_factory=list)


class FakeOpenAISDK:
    version = "fake-openai-1"

    def __init__(self, *, drop_attached_tools: bool = False, drift_tool_schema: bool = False) -> None:
        self.drop_attached_tools = drop_attached_tools
        self.drift_tool_schema = drift_tool_schema

    def create_agent(
        self,
        *,
        name: str,
        instructions: str,
        model: str,
        model_options: Mapping[str, object],
        output_type: type[object],
        tools: tuple[object, ...],
    ) -> object:
        return FakeAgent(name, instructions, model, model_options, output_type, list(tools))

    def create_function_tool(
        self,
        *,
        name: str,
        description: str,
        implementation: object,
        input_type: type[object] | None,
        output_adapter: object,
        requires_approval: bool,
    ) -> object:
        del description, output_adapter
        schema = dict(self.input_schema(input_type))
        if self.drift_tool_schema:
            properties = cast(dict[str, object], schema.get("properties", {}))
            if properties:
                first = next(iter(properties))
                properties[first] = {"type": "string"}
        return FakeTool(
            openai_tool_name(name),
            schema,
            implementation,
            requires_approval,
        )

    def create_hosted_tool(self, *, name: str, binding: BindingEntry) -> object:
        return FakeTool(name, implementation=binding)

    def create_delegate_tool(
        self,
        *,
        name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
    ) -> object:
        del description, child
        return FakeTool(
            openai_tool_name(name),
            self.input_schema(input_type),
            input_type=input_type,
        )

    def create_isolated_delegate_tool(
        self,
        *,
        name: str,
        description: str,
        child: object,
        input_type: type[object] | None,
        isolation_id: SemanticId,
        requested_dimensions: FrozenMap[str, str],
        declared_capabilities: tuple[str, ...],
        environment: EnvironmentProvider,
    ) -> object:
        del description, child
        return FakeTool(
            openai_tool_name(name),
            self.input_schema(input_type),
            environment=environment,
            isolation_id=isolation_id,
            dimensions=requested_dimensions,
            declared_capabilities=declared_capabilities,
            input_type=input_type,
        )

    def create_handoff(
        self,
        *,
        name: str,
        description: str,
        child: object,
        history: str,
    ) -> object:
        del description
        return FakeHandoff(name, child, history)

    def attach(self, agent: object, *, tools: tuple[object, ...], handoffs: tuple[object, ...]) -> None:
        assert isinstance(agent, FakeAgent)
        agent.tools = [] if self.drop_attached_tools else list(tools)
        agent.handoffs = list(handoffs)

    def describe(self, agent: object) -> NativeAgentDescription:
        assert isinstance(agent, FakeAgent)
        return NativeAgentDescription(
            agent.name,
            agent.instructions,
            agent.model,
            agent.output_type,
            self.output_schema(agent.output_type),
            tuple(agent.tools),
            tuple(agent.handoffs),
        )

    def describe_tool(self, tool: object) -> NativeToolDescription:
        assert isinstance(tool, FakeTool)
        return NativeToolDescription(tool.name, tool.input_schema)

    def input_schema(self, input_type: type[object] | None) -> Mapping[str, object]:
        if input_type is None:
            return {"type": "object", "properties": {}, "additionalProperties": False}
        return cast(dict[str, object], cast(Any, input_type).model_json_schema())

    def output_schema(self, output_type: type[object]) -> Mapping[str, object]:
        return cast(dict[str, object], cast(Any, output_type).model_json_schema())


def write_project(
    tmp_path: Path,
    *,
    isolation: bool = False,
    network: str | None = None,
    filesystem: str | None = None,
    strong_environment: bool = False,
    datasource_cache: str = "run",
    async_current: bool = False,
    invalid_current: bool = False,
    operational_source: str = "",
) -> None:
    sys.modules.pop("app_impl", None)
    isolation_source = ""
    edge_isolation = ""
    environments = ""
    profile_options = ""
    if isolation:
        network_line = f"    network = {network}\n" if network is not None else ""
        filesystem_line = f"    filesystem = {filesystem}\n" if filesystem is not None else ""
        isolation_source = f"""\
isolation CleanContext:
    context = explicit_only
    capabilities = declared_only
    state = fresh
{filesystem_line}{network_line}    return = final_output_only

"""
        edge_isolation = "    isolation = CleanContext\n"
        provider_locator = (
            "app_impl:StrongEnvironment" if strong_environment else "contract4agents.runtime:InProcessEnvironment"
        )
        environments = f"""\
[targets.openai.environments.in_process]
provider = "{provider_locator}"

"""
        profile_options = """\
[targets.openai.profiles.test.options]
environment = "in_process"
"""

    (tmp_path / "system.contract").write_text(
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
    cache = {datasource_cache}

external_context request_context -> Request:
    description = "Invocation metadata."
    sensitivity = internal
    render = markdown

{isolation_source}{operational_source}agent Child(request: Request) -> Result:
    use records.lookup:
        availability = enabled
        authorization = approval_required
        execution = host
    context current: Result from datasource records.current:
        map query = input.request.value
    context metadata: Request from external request_context
    goal = "Use the declared lookup tool."

agent Reviewer(request: Request) -> Result:
    goal = "Review the result."

agent Parent(request: Request) -> Result:
    goal = "Delegate and review."

composition ask_child from Parent to Child:
    mode = delegate
    description = "Ask the child for a result."
    history = none
    map request = input.request
{edge_isolation}
composition send_review from Parent to Reviewer:
    mode = handoff
    description = "Hand the conversation to the reviewer."
    history = full
    map request = input.request
"""
    )
    strong_environment_source = ""
    if strong_environment:
        strong_environment_source = """\
from contract4agents.ir import FrozenMap
from contract4agents.planning import MappingSupport
from contract4agents.runtime import EnvironmentEnforcementEvidence

class StrongEnvironment:
    provider_id = "app_impl:StrongEnvironment"

    def planning_support(self):
        return {
            "context:explicit_only": MappingSupport("emulated", "test_sandbox.context"),
            "capabilities:declared_only": MappingSupport("emulated", "test_sandbox.capabilities"),
            "state:fresh": MappingSupport("emulated", "test_sandbox.state"),
            "filesystem:none": MappingSupport("host_enforced", "test_sandbox.filesystem"),
            "network:denied": MappingSupport("host_enforced", "test_sandbox.network"),
            "return:final_output_only": MappingSupport("emulated", "test_sandbox.return"),
        }

    def enforcement_evidence(self, plan):
        return EnvironmentEnforcementEvidence(
            plan.id,
            plan.environment,
            self.provider_id,
            FrozenMap((name, value.requested) for name, value in plan.dimensions.items()),
            FrozenMap((name, value.mechanism or "") for name, value in plan.dimensions.items()),
        )

    async def run(self, request, invoke):
        return await invoke(
            request.input_payload,
            None,
            object(),
            request.declared_capabilities,
        )

"""
    current_prefix = "async " if async_current else ""
    current_result = '{"wrong": query}' if invalid_current else '{"value": query}'
    (tmp_path / "app_impl.py").write_text(
        f"""\
{strong_environment_source}
def lookup(query):
    return {{"value": query}}

{current_prefix}def current(query):
    return {current_result}

def context():
    return {{"value": "context"}}
"""
    )
    (tmp_path / "contract4agents.targets.toml").write_text(
        f"""\
schema_version = "1"

[targets.openai]
adapter = "openai"

[targets.openai.tools."records.lookup"]
python = "app_impl:lookup"

[targets.openai.datasources."records.current"]
python = "app_impl:current"

[targets.openai.external_context.request_context]
python = "app_impl:context"

{environments}[targets.openai.profiles.test]
default_model = "test-model"

{profile_options}"""
    )


__all__ = ["FakeAgent", "FakeHandoff", "FakeOpenAISDK", "FakeTool", "write_project"]
