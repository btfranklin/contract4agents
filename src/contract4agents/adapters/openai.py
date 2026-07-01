"""OpenAI Agents SDK adapter.

The adapter is intentionally thin: Contract4Agents compiles to provider-neutral
manifests first, and this module projects those manifests onto OpenAI's SDK.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from contract4agents.adapters._openai_hosted_tools import hosted_tool_from_registry, looks_like_sdk_tool
from contract4agents.adapters._openai_names import contract_tool_name, openai_tool_name
from contract4agents.adapters._openai_output_types import build_openai_output_type_registry
from contract4agents.adapters._openai_run import run_openai_agent, run_openai_agent_with_contract
from contract4agents.adapters._openai_sdk import build_openai_agent
from contract4agents.adapters._openai_semantic import OpenAISemanticJudge
from contract4agents.adapters._openai_trace import OpenAITraceHooks
from contract4agents.adapters._openai_types import (
    OpenAIAdapterPlan,
    OpenAIAdapterResult,
    OpenAIAdapterUnavailable,
    OpenAIAgentFactoryCaveat,
    OpenAIAgentFactoryError,
    OpenAIAgentFactoryResult,
    OpenAIAgentPlan,
    OpenAIApprovalRequest,
    OpenAICompositionPlan,
    OpenAIContractRunResult,
    OpenAIHostedToolPlan,
    OpenAIToolPlan,
    OpenAIToolRegistration,
)
from contract4agents.compiler import (
    AgentManifest,
    CompilerArtifacts,
)
from contract4agents.composition import parse_composition_declaration
from contract4agents.guards import GuardPlanItem


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

    guard_plan_by_agent = _guard_plan_by_agent(artifacts["guard_plan"])
    plans: dict[str, OpenAIAgentPlan] = {}
    for agent_name, manifest in artifacts["manifests"].items():
        agent_caveats: list[OpenAIAgentFactoryCaveat] = []
        agent_guard_plan = guard_plan_by_agent.get(agent_name, [])
        model = model_registry.get(agent_name, default_model)
        if model is None:
            raise OpenAIAgentFactoryError(f"No model configured for agent `{agent_name}`")
        output_type_name = manifest["output"]["type"]
        if output_type_name not in output_types:
            raise OpenAIAgentFactoryError(
                f"No output type registered for `{output_type_name}` used by agent `{agent_name}`"
            )
        _validate_output_guards(agent_name, output_type_name, output_types, agent_guard_plan, agent_caveats)
        _collect_guard_caveats(agent_name, manifest, agent_guard_plan, agent_caveats)
        host_tools = _planned_tools(agent_name, manifest, tool_registry, agent_guard_plan, agent_caveats)
        hosted_tools = _planned_hosted_tools(agent_name, manifest, hosted_tool_registry, agent_caveats)
        composition = _planned_composition(agent_name, manifest, agent_tool_registry, handoff_registry, agent_caveats)
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


def _planned_tools(
    agent_name: str,
    manifest: AgentManifest,
    tool_registry: Mapping[str, Any] | None,
    guard_plan: list[GuardPlanItem],
    caveats: list[OpenAIAgentFactoryCaveat],
) -> list[OpenAIToolPlan]:
    tools: list[OpenAIToolPlan] = []
    denied_tools = {item["target"] for item in guard_plan if item["kind"] == "denied_tool" and item["target"]}
    approval_tools = {
        item["target"] for item in guard_plan if item["kind"] == "approval_required_tool" and item["target"]
    }
    for tool in manifest["tools"]:
        name = tool["name"]
        if name in denied_tools or tool["permission"] == "denied":
            caveats.append(
                OpenAIAgentFactoryCaveat(
                    agent_name,
                    "denied_tool_omitted",
                    f"Tool `{name}` is denied and was omitted from the OpenAI Agent.",
                )
            )
            continue
        if not tool_registry or name not in tool_registry:
            raise OpenAIAgentFactoryError(f"No host tool registered for `{name}` used by agent `{agent_name}`")
        requires_approval = tool["permission"] == "requires_approval" or name in approval_tools
        sdk_tool, wrapped = _tool_from_registry(agent_name, name, tool_registry[name], requires_approval, caveats)
        tools.append(
            OpenAIToolPlan(
                agent=agent_name,
                name=name,
                permission=tool["permission"],
                sdk_name=openai_tool_name(name),
                tool=sdk_tool,
                source=tool["module"],
                wrapped=wrapped,
                requires_approval=requires_approval,
            )
        )
    return tools


def _tool_from_registry(
    agent_name: str,
    name: str,
    registry_entry: Any,
    requires_approval: bool,
    caveats: list[OpenAIAgentFactoryCaveat],
) -> tuple[Any, bool]:
    if isinstance(registry_entry, OpenAIToolRegistration):
        if registry_entry.raw_callable:
            return _wrap_callable_tool(name, registry_entry.value, requires_approval, registry_entry.description), True
        if requires_approval:
            caveats.append(_approval_unverified_caveat(agent_name, name))
        return registry_entry.value, False
    if callable(registry_entry) and not looks_like_sdk_tool(registry_entry):
        return _wrap_callable_tool(name, registry_entry, requires_approval, None), True
    if requires_approval:
        caveats.append(_approval_unverified_caveat(agent_name, name))
    return registry_entry, False


def _wrap_callable_tool(
    name: str,
    func: Any,
    requires_approval: bool,
    description: str | None,
) -> Any:
    try:
        from agents import function_tool
    except Exception as exc:  # noqa: BLE001 - optional adapter import boundary.
        raise OpenAIAdapterUnavailable("openai-agents is not installed") from exc
    kwargs: dict[str, Any] = {
        "name_override": openai_tool_name(name),
        "needs_approval": requires_approval,
    }
    if description:
        kwargs["description_override"] = description
    return function_tool(**kwargs)(func)


def _approval_unverified_caveat(agent_name: str, name: str) -> OpenAIAgentFactoryCaveat:
    return OpenAIAgentFactoryCaveat(
        agent_name,
        "approval_enforcement_unverified",
        f"Tool `{name}` requires approval, but the registered SDK tool was not wrapped by Contract4Agents.",
    )


def _planned_hosted_tools(
    agent_name: str,
    manifest: AgentManifest,
    hosted_tool_registry: Mapping[str, Any] | None,
    caveats: list[OpenAIAgentFactoryCaveat],
) -> list[OpenAIHostedToolPlan]:
    tools: list[OpenAIHostedToolPlan] = []
    for hosted_tool in manifest["hosted_tools"]:
        name = hosted_tool["name"]
        if hosted_tool["permission"] == "denied":
            caveats.append(
                OpenAIAgentFactoryCaveat(
                    agent_name,
                    "denied_hosted_tool_omitted",
                    f"Hosted tool `{name}` is declared denied and was omitted from the OpenAI Agent.",
                )
            )
            continue
        if not hosted_tool_registry or name not in hosted_tool_registry:
            raise OpenAIAgentFactoryError(
                f"No hosted tool registered for `{name}` used by agent `{agent_name}`"
            )
        tools.append(
            OpenAIHostedToolPlan(
                agent=agent_name,
                name=name,
                provider=hosted_tool["provider"],
                tool_name=hosted_tool["tool"],
                config=dict(hosted_tool["config"]),
                permission=hosted_tool["permission"],
                tool=hosted_tool_from_registry(name, hosted_tool["config"], hosted_tool_registry[name]),
            )
        )
    return tools


def _planned_composition(
    agent_name: str,
    manifest: AgentManifest,
    agent_tool_registry: Mapping[str, Any] | None,
    handoff_registry: Mapping[str, Any] | None,
    caveats: list[OpenAIAgentFactoryCaveat],
) -> list[OpenAICompositionPlan]:
    plans: list[OpenAICompositionPlan] = []
    declarations = _composition_declarations(manifest["composition"])
    for dependency in manifest["agents"]:
        child = dependency["name"]
        declared_mode = declarations.get(child)
        if declared_mode == "isolated_subagent":
            caveats.append(
                OpenAIAgentFactoryCaveat(
                    agent_name,
                    "unsupported_composition",
                    f"Composition mode `isolated_subagent({child})` has no OpenAI adapter mapping.",
                )
            )
            plans.append(OpenAICompositionPlan(agent_name, child, "unsupported", source="isolated_subagent"))
            continue
        if declared_mode == "agent_as_tool":
            plans.append(_agent_tool_composition(agent_name, child, agent_tool_registry, caveats, declared_mode))
            continue
        if declared_mode == "handoff":
            plans.append(_handoff_composition(agent_name, child, handoff_registry, caveats, "handoff"))
            continue
        caveats.append(
            OpenAIAgentFactoryCaveat(
                agent_name,
                "agent_dependency_unwired",
                f"Declared agent dependency `{child}` has no explicit composition mapping.",
            )
        )
        plans.append(OpenAICompositionPlan(agent_name, child, "unwired", source="undeclared"))
    return plans


def _agent_tool_composition(
    agent_name: str,
    child: str,
    registry: Mapping[str, Any] | None,
    caveats: list[OpenAIAgentFactoryCaveat],
    source: str,
) -> OpenAICompositionPlan:
    if registry and child in registry:
        return OpenAICompositionPlan(agent_name, child, "agent_as_tool", registry[child], source=source)
    caveats.append(
        OpenAIAgentFactoryCaveat(
            agent_name,
            "agent_tool_missing",
            f"Composition requires `{child}` as an agent tool, but no agent-tool registration was supplied.",
        )
    )
    return OpenAICompositionPlan(agent_name, child, "unwired", source=source)


def _handoff_composition(
    agent_name: str,
    child: str,
    registry: Mapping[str, Any] | None,
    caveats: list[OpenAIAgentFactoryCaveat],
    source: str,
) -> OpenAICompositionPlan:
    if registry and child in registry:
        return OpenAICompositionPlan(agent_name, child, "handoff", registry[child], source=source)
    caveats.append(
        OpenAIAgentFactoryCaveat(
            agent_name,
            "handoff_missing",
            f"Composition requires `{child}` as a handoff, but no handoff registration was supplied.",
        )
    )
    return OpenAICompositionPlan(agent_name, child, "unwired", source=source)


def _composition_declarations(items: list[str]) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for item in items:
        declaration = parse_composition_declaration(item)
        if declaration:
            declarations[declaration.agent] = declaration.mode
    return declarations


def _guard_plan_by_agent(guard_plan: list[GuardPlanItem]) -> dict[str, list[GuardPlanItem]]:
    result: dict[str, list[GuardPlanItem]] = {}
    for item in guard_plan:
        result.setdefault(item["agent"], []).append(item)
    return result


def _validate_output_guards(
    agent_name: str,
    output_type_name: str,
    output_type_registry: Mapping[str, Any],
    guard_plan: list[GuardPlanItem],
    caveats: list[OpenAIAgentFactoryCaveat],
) -> None:
    for item in guard_plan:
        if item["kind"] != "output_conformance":
            continue
        output_type = item["output_type"]
        if output_type is None:
            continue
        if output_type not in output_type_registry:
            raise OpenAIAgentFactoryError(
                f"No output type registered for guard `{item['expression']}` used by agent `{agent_name}`"
            )
        if output_type != output_type_name:
            caveats.append(
                OpenAIAgentFactoryCaveat(
                    agent_name,
                    "output_guard_type_mismatch",
                    f"Guard `{item['expression']}` references `{output_type}` "
                    f"but agent output is `{output_type_name}`.",
                )
            )


def _collect_guard_caveats(
    agent_name: str,
    manifest: AgentManifest,
    guard_plan: list[GuardPlanItem],
    caveats: list[OpenAIAgentFactoryCaveat],
) -> None:
    tool_permissions = {tool["name"]: tool["permission"] for tool in manifest["tools"]}
    for item in guard_plan:
        if item["kind"] == "unsupported":
            caveats.append(
                OpenAIAgentFactoryCaveat(
                    agent_name,
                    "unsupported_guard",
                    f"Guard `{item['expression']}` has no OpenAI adapter mapping: {item['message']}",
                )
            )
            continue
        if item["kind"] == "approval_required_tool":
            target = item["target"]
            if target and tool_permissions.get(target) != "requires_approval":
                caveats.append(
                    OpenAIAgentFactoryCaveat(
                        agent_name,
                        "guard_permission_mismatch",
                        f"Guard `{item['expression']}` requires approval but manifest permission is "
                        f"`{tool_permissions.get(target)}`.",
                    )
                )
            continue
        if item["kind"] == "denied_tool":
            target = item["target"]
            if target and tool_permissions.get(target) != "denied":
                caveats.append(
                    OpenAIAgentFactoryCaveat(
                        agent_name,
                        "guard_permission_mismatch",
                        f"Guard `{item['expression']}` denies a tool whose manifest permission is "
                        f"`{tool_permissions.get(target)}`.",
                    )
                )


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
    "OpenAIAdapterPlan",
    "OpenAIAdapterResult",
    "OpenAIAdapterUnavailable",
    "OpenAIAgentFactoryCaveat",
    "OpenAIAgentFactoryError",
    "OpenAIAgentFactoryResult",
    "OpenAIAgentPlan",
    "OpenAIApprovalRequest",
    "OpenAICompositionPlan",
    "OpenAIContractRunResult",
    "OpenAIHostedToolPlan",
    "OpenAISemanticJudge",
    "OpenAIToolPlan",
    "OpenAIToolRegistration",
    "OpenAITraceHooks",
    "build_openai_agent",
    "build_openai_agents_from_contracts",
    "build_openai_agents_from_plan",
    "build_openai_output_type_registry",
    "contract_tool_name",
    "openai_tool_name",
    "plan_openai_agents_from_contracts",
    "run_openai_agent",
    "run_openai_agent_with_contract",
]
