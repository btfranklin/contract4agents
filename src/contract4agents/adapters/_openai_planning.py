"""OpenAI adapter planning entrypoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from contract4agents.adapters._openai_composition import planned_composition
from contract4agents.adapters._openai_guards import (
    collect_guard_caveats,
    guard_plan_by_agent,
    validate_output_guards,
)
from contract4agents.adapters._openai_output_types import build_openai_output_type_registry
from contract4agents.adapters._openai_sdk import build_openai_agent
from contract4agents.adapters._openai_tools import planned_hosted_tools, planned_tools
from contract4agents.adapters._openai_types import (
    OpenAIAdapterPlan,
    OpenAIAgentFactoryCaveat,
    OpenAIAgentFactoryError,
    OpenAIAgentFactoryResult,
    OpenAIAgentPlan,
)
from contract4agents.compiler import AgentManifest, CompilerArtifacts


def plan_openai_agents_from_contracts(
    artifacts: CompilerArtifacts,
    *,
    output_type_registry: Mapping[str, Any] | None = None,
    model_registry: Mapping[str, Any],
    tool_registry: Mapping[str, Any] | None = None,
    hosted_tool_registry: Mapping[str, Any] | None = None,
    agent_tool_registry: Mapping[str, Any] | None = None,
    handoff_registry: Mapping[str, Any] | None = None,
    instruction_overrides: Mapping[str, str] | None = None,
    default_model: Any | None = None,
    generate_output_types: bool = False,
) -> OpenAIAdapterPlan:
    """Create an inspectable OpenAI adapter plan from compiled Contract4Agents artifacts."""
    caveats: list[OpenAIAgentFactoryCaveat] = []
    output_types: dict[str, Any] = {}
    if generate_output_types:
        output_types.update(build_openai_output_type_registry(artifacts))
    if output_type_registry:
        output_types.update(output_type_registry)

    guards_by_agent = guard_plan_by_agent(artifacts["guard_plan"])
    plans: dict[str, OpenAIAgentPlan] = {}
    for agent_name, manifest in artifacts["manifests"].items():
        agent_caveats: list[OpenAIAgentFactoryCaveat] = []
        agent_guard_plan = guards_by_agent.get(agent_name, [])
        model = model_registry.get(agent_name, default_model)
        if model is None:
            raise OpenAIAgentFactoryError(f"No model configured for agent `{agent_name}`")
        output_type_name = manifest["output"]["type"]
        if output_type_name not in output_types:
            raise OpenAIAgentFactoryError(
                f"No output type registered for `{output_type_name}` used by agent `{agent_name}`"
            )
        validate_output_guards(agent_name, output_type_name, output_types, agent_guard_plan, agent_caveats)
        collect_guard_caveats(agent_name, manifest, agent_guard_plan, agent_caveats)
        host_tools = planned_tools(agent_name, manifest, tool_registry, agent_guard_plan, agent_caveats)
        hosted_tools = planned_hosted_tools(agent_name, manifest, hosted_tool_registry, agent_caveats)
        composition = planned_composition(
            agent_name, manifest, agent_tool_registry, handoff_registry, agent_caveats
        )
        caveats.extend(agent_caveats)
        plans[agent_name] = OpenAIAgentPlan(
            agent=agent_name,
            manifest=manifest,
            source_path=manifest["source_path"],
            instruction_ref=f"instructions/{agent_name}.md",
            instructions=_instructions_for(agent_name, artifacts, instruction_overrides),
            model=model,
            output_type_name=output_type_name,
            output_schema_ref=manifest["output"]["schema_ref"],
            output_type=output_types[output_type_name],
            tools=host_tools,
            hosted_tools=hosted_tools,
            composition=composition,
            inputs=list(manifest["inputs"]),
            datasources=list(manifest["datasources"]),
            guards=agent_guard_plan,
            assertions=list(manifest["assertions"]),
            caveats=agent_caveats,
        )
    return OpenAIAdapterPlan(artifacts, plans, caveats)


def build_openai_agents_from_plan(plan: OpenAIAdapterPlan) -> OpenAIAgentFactoryResult:
    """Build OpenAI Agents SDK objects from a previously inspected adapter plan."""
    agents: dict[str, Any] = {}
    for agent_name, agent_plan in plan.agents.items():
        manifest_with_model = dict(agent_plan.manifest)
        manifest_with_model["model"] = agent_plan.model
        tools = [item.tool for item in agent_plan.tools]
        tools.extend(item.tool for item in agent_plan.hosted_tools)
        tools.extend(item.sdk_object for item in agent_plan.composition if item.mode == "agent_as_tool")
        handoffs = [item.sdk_object for item in agent_plan.composition if item.mode == "handoff"]
        agents[agent_name] = build_openai_agent(
            cast(AgentManifest, manifest_with_model),
            agent_plan.instructions,
            tools=[item for item in tools if item is not None],
            handoffs=[item for item in handoffs if item is not None],
            output_type=agent_plan.output_type,
        )
    return OpenAIAgentFactoryResult(agents, plan.caveats, plan)


def build_openai_agents_from_contracts(
    artifacts: CompilerArtifacts,
    *,
    output_type_registry: Mapping[str, Any] | None = None,
    model_registry: Mapping[str, Any],
    tool_registry: Mapping[str, Any] | None = None,
    hosted_tool_registry: Mapping[str, Any] | None = None,
    agent_tool_registry: Mapping[str, Any] | None = None,
    handoff_registry: Mapping[str, Any] | None = None,
    instruction_overrides: Mapping[str, str] | None = None,
    default_model: Any | None = None,
    generate_output_types: bool = False,
) -> OpenAIAgentFactoryResult:
    """Build OpenAI Agents SDK objects from compiled artifacts plus explicit registries."""
    plan = plan_openai_agents_from_contracts(
        artifacts,
        output_type_registry=output_type_registry,
        model_registry=model_registry,
        tool_registry=tool_registry,
        hosted_tool_registry=hosted_tool_registry,
        agent_tool_registry=agent_tool_registry,
        handoff_registry=handoff_registry,
        instruction_overrides=instruction_overrides,
        default_model=default_model,
        generate_output_types=generate_output_types,
    )
    return build_openai_agents_from_plan(plan)


def _instructions_for(
    agent_name: str,
    artifacts: CompilerArtifacts,
    instruction_overrides: Mapping[str, str] | None,
) -> str:
    if instruction_overrides and agent_name in instruction_overrides:
        return instruction_overrides[agent_name]
    try:
        return artifacts["instructions"][agent_name]
    except KeyError as exc:
        raise OpenAIAgentFactoryError(f"No instructions compiled for agent `{agent_name}`") from exc


__all__ = [
    "build_openai_agents_from_contracts",
    "build_openai_agents_from_plan",
    "plan_openai_agents_from_contracts",
]
