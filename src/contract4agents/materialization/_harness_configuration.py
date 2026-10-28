"""Offline AgentCore Harness configuration from one reviewed plan."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import BaseModel, TypeAdapter

from contract4agents.adapters._native_names import native_name
from contract4agents.ir import FrozenJsonValue, FrozenMap, SemanticId, freeze_json
from contract4agents.materialization._types import (
    GeneratedType,
    build_agent_input_types,
    build_parameter_model,
    build_pydantic_types,
    type_adapter_for,
)
from contract4agents.planning import PlannedSystem
from contract4agents.target_bindings import TargetBindings


def canonical_json(value: object) -> str:
    """Encode finite JSON with stable key order."""

    return json.dumps(thaw(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: thaw(child) for key, child in value.items()}
    if isinstance(value, tuple | list):
        return [thaw(child) for child in value]
    return value


def frozen_object(value: object) -> FrozenMap[str, FrozenJsonValue]:
    frozen = freeze_json(value)
    if not isinstance(frozen, FrozenMap):
        raise ValueError("Expected a JSON object")
    return frozen


@dataclass(frozen=True)
class HarnessTool:
    """A declared inline tool and its strict client boundary."""

    semantic_id: SemanticId
    name: str
    description: str
    input_type: type[BaseModel] | None
    output_adapter: TypeAdapter[object]
    grant_id: SemanticId | None = None
    child_agent_id: SemanticId | None = None
    approval_required: bool = False
    side_effect: bool = False
    input_mappings: FrozenMap[str, str] = FrozenMap()

    @property
    def input_schema(self) -> dict[str, object]:
        if self.input_type is None:
            return {"type": "object", "properties": {}, "additionalProperties": False}
        return self.input_type.model_json_schema()

    def definition(self) -> dict[str, object]:
        return {
            "type": "inline_function",
            "name": self.name,
            "config": {"inlineFunction": {"description": self.description, "inputSchema": self.input_schema}},
        }


@dataclass(frozen=True)
class HarnessAgentConfiguration:
    """One AWS CreateHarness request and its client validation types."""

    agent_id: SemanticId
    request: FrozenMap[str, FrozenJsonValue]
    input_type: type[BaseModel] | None
    output_adapter: TypeAdapter[object]
    tools: FrozenMap[str, HarnessTool]

    @property
    def configuration_digest(self) -> str:
        return content_digest(self.request)

    def request_payload(self) -> dict[str, object]:
        """Return a mutable copy of the derived AWS CLI request."""

        return cast(dict[str, object], thaw(self.request))


@dataclass(frozen=True)
class HarnessConfiguration:
    """Derived deployment requests; generation makes no AWS calls."""

    system: PlannedSystem
    agents: FrozenMap[str, HarnessAgentConfiguration]

    def write(self, directory: Path | str) -> None:
        """Write AWS CLI input files and an identity manifest."""

        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, object] = {}
        for name, agent in self.agents.items():
            filename = f"{name}.harness.json"
            (destination / filename).write_text(json.dumps(thaw(agent.request), indent=2, sort_keys=True) + "\n")
            manifest[name] = {"file": filename, "configuration_digest": agent.configuration_digest}
        (destination / "manifest.json").write_text(
            json.dumps(
                {
                    "contract_digest": self.system.plan.contract_digest,
                    "plan_digest": self.system.plan.plan_digest,
                    "agents": manifest,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )


def build_harness_configuration(
    system: PlannedSystem,
    *,
    input_types: FrozenMap[SemanticId, type[BaseModel] | None] | None = None,
    output_types: FrozenMap[str, GeneratedType] | None = None,
) -> HarnessConfiguration:
    """Derive AWS CLI CreateHarness input from the selected plan."""

    if system.plan.adapter.name != "agentcore_harness":
        raise ValueError("Harness configuration requires an agentcore_harness plan")
    ir, plan = system.ir, system.plan
    if ir.contexts or ir.isolation_profiles:
        raise ValueError("Harness does not yet support declared context or isolation profiles")
    output_types = output_types if output_types is not None else build_pydantic_types(ir)
    input_types = input_types if input_types is not None else build_agent_input_types(ir, output_types)
    template = (Path(__file__).parents[1] / "adapters/prompts/agentcore_harness/structured_output.md").read_text()
    agents: dict[str, HarnessAgentConfiguration] = {}
    harness_names: set[str] = set()
    for agent_id, agent in ir.agents.items():
        tools: dict[str, HarnessTool] = {}
        for grant_id in agent.grant_ids:
            grant = ir.grants[grant_id]
            if grant.availability != "enabled":
                continue
            capability = ir.capabilities[grant.capability_id]
            locator = plan.bindings[capability.id].locator
            name = str(locator.get("tool_name", native_name("tool", capability.id)))
            boundary = HarnessTool(
                capability.id,
                name,
                capability.description,
                build_parameter_model(f"{name}Input", capability.parameters, output_types),
                type_adapter_for(capability.output_type, output_types),
                grant_id=grant_id,
                approval_required=grant.authorization == "approval_required",
                side_effect=bool(capability.side_effect),
            )
            _add_tool(tools, boundary)
        for edge_id, edge in ir.composition.items():
            if edge.source_agent_id != agent_id:
                continue
            if edge.mode != "delegate" or edge.history != "none" or edge.isolation_id is not None:
                raise ValueError("Harness supports only delegation with no shared history or explicit isolation")
            child = ir.agents[edge.target_agent_id]
            _add_tool(
                tools,
                HarnessTool(
                    edge_id,
                    native_name("delegate", edge_id),
                    edge.description,
                    input_types[child.id],
                    type_adapter_for(child.output_type, output_types),
                    child_agent_id=child.id,
                    input_mappings=edge.input_mappings,
                ),
            )
        if not tools or len(tools) > 64:
            raise ValueError("Harness requires between one and 64 explicit inline tools")
        options = plan.agents[agent_id].model_options
        harness_name = options.get("harness_name", native_name("agent", agent_id, max_length=40))
        if not isinstance(harness_name, str) or harness_name in harness_names:
            raise ValueError("Harness names must be unique strings")
        harness_names.add(harness_name)
        max_tokens = options.get("max_tokens", 4096)
        model: dict[str, object] = {
            "modelId": plan.agents[agent_id].model,
            "apiFormat": "converse_stream",
            "maxTokens": max_tokens,
        }
        for key, aws_key in (("temperature", "temperature"), ("top_p", "topP")):
            if key in options:
                model[aws_key] = options[key]
        output_adapter = type_adapter_for(agent.output_type, output_types)
        instructions = (
            system.artifacts.instructions[agent.name]
            + "\n\n"
            + template.replace("{{output_schema}}", canonical_json(output_adapter.json_schema()))
        )
        request: dict[str, object] = {
            "harnessName": harness_name,
            "executionRoleArn": options["execution_role_arn"],
            "model": {"bedrockModelConfig": model},
            "systemPrompt": [{"text": instructions}],
            "tools": [tool.definition() for tool in tools.values()],
            "allowedTools": list(tools),
            "memory": {"disabled": {}},
            "truncation": {"strategy": "none"},
            "maxIterations": options.get("max_iterations", 10),
            "maxTokens": max_tokens,
            "timeoutSeconds": options.get("timeout_seconds", 120),
            "tags": {"contract4agents:contract": plan.contract_digest, "contract4agents:plan": plan.plan_digest},
        }
        agents[agent.name] = HarnessAgentConfiguration(
            agent_id,
            frozen_object(request),
            input_types[agent_id],
            output_adapter,
            FrozenMap(tools),
        )
    return HarnessConfiguration(system, FrozenMap(agents))


def _add_tool(tools: dict[str, HarnessTool], tool: HarnessTool) -> None:
    if tool.name in tools or tool.name in {"shell", "file_operations"}:
        raise ValueError("Harness tool names must be unique and cannot name built-in tools")
    tools[tool.name] = tool


def generate_harness_configuration(
    root: Path | str,
    *,
    target: str,
    profile: str,
    bindings: TargetBindings | Path | str | None = None,
) -> HarnessConfiguration:
    """Plan and generate configuration without AWS access."""

    from contract4agents.materialization._entrypoint import plan_project

    return build_harness_configuration(plan_project(root, target=target, profile=profile, bindings=bindings))
