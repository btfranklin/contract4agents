"""OpenAI Agents SDK adapter.

The adapter is intentionally thin: Contract4Agents compiles to provider-neutral
manifests first, and this module projects those manifests onto OpenAI's SDK.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Awaitable, Mapping
from typing import Any, cast

from contract4agents.adapters._openai_output_types import build_openai_output_type_registry
from contract4agents.adapters._openai_types import (
    ApprovalCallback,
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
from contract4agents.assertions import RunEvaluationResult, evaluate_run_contract
from contract4agents.compiler import (
    AgentManifest,
    CompilerArtifacts,
)
from contract4agents.composition import parse_composition_declaration
from contract4agents.guards import GuardPlanItem
from contract4agents.hosted_tools import hosted_tool_kwargs
from contract4agents.runtime import RuntimeContext, TraceRecorder

_RunHooksBase: type[Any]
try:
    from agents import RunHooks as _ImportedRunHooks

    _RunHooksBase = _ImportedRunHooks
except Exception:  # noqa: BLE001 - optional adapter import boundary.
    _RunHooksBase = object


_OPENAI_TOOL_NAME_PREFIX = "c4a_"


def openai_tool_name(contract_name: str) -> str:
    """Convert a Contract4Agents capability name into an OpenAI-safe tool name."""
    return _OPENAI_TOOL_NAME_PREFIX + "".join(f"{len(part)}_{part}" for part in contract_name.split("."))


def contract_tool_name(openai_name: str) -> str:
    """Convert a generated OpenAI tool name back into the Contract4Agents capability name."""
    if not openai_name.startswith(_OPENAI_TOOL_NAME_PREFIX):
        if "__" in openai_name:
            raise OpenAIAgentFactoryError(
                f"OpenAI tool name `{openai_name}` uses ambiguous legacy Contract4Agents encoding"
            )
        return openai_name

    encoded = openai_name[len(_OPENAI_TOOL_NAME_PREFIX) :]
    parts: list[str] = []
    cursor = 0
    while cursor < len(encoded):
        delimiter = encoded.find("_", cursor)
        if delimiter == -1 or delimiter == cursor:
            raise OpenAIAgentFactoryError(f"OpenAI tool name `{openai_name}` is not valid Contract4Agents encoding")
        raw_length = encoded[cursor:delimiter]
        if not raw_length.isdigit():
            raise OpenAIAgentFactoryError(f"OpenAI tool name `{openai_name}` is not valid Contract4Agents encoding")
        length = int(raw_length)
        start = delimiter + 1
        end = start + length
        if end > len(encoded):
            raise OpenAIAgentFactoryError(f"OpenAI tool name `{openai_name}` is not valid Contract4Agents encoding")
        parts.append(encoded[start:end])
        cursor = end
    return ".".join(parts)


class OpenAITraceHooks(_RunHooksBase):  # type: ignore[misc]
    """Minimal hook object that normalizes Agents SDK lifecycle events to Contract4Agents traces."""

    def __init__(self, trace: TraceRecorder) -> None:
        super().__init__()
        self.trace = trace

    async def on_agent_start(self, _context: Any, agent: Any) -> None:
        self.trace.record("agent.started", agent=getattr(agent, "name", str(agent)))

    async def on_agent_end(self, _context: Any, agent: Any, output: Any) -> None:
        self.trace.record("agent.completed", agent=getattr(agent, "name", str(agent)), output=_serializable(output))

    async def on_handoff(self, _context: Any, from_agent: Any, to_agent: Any) -> None:
        self.trace.record(
            "agent.handoff",
            from_agent=getattr(from_agent, "name", str(from_agent)),
            to_agent=getattr(to_agent, "name", str(to_agent)),
        )

    async def on_tool_start(self, _context: Any, agent: Any, tool: Any) -> None:
        tool_name = _normalized_tool_name(tool)
        event_type = "hosted_tool.started" if _is_hosted_sdk_tool(tool) else "tool.started"
        self.trace.record(
            event_type,
            agent=getattr(agent, "name", str(agent)),
            tool=tool_name,
        )

    async def on_tool_end(self, _context: Any, agent: Any, tool: Any, result: str) -> None:
        tool_name = _normalized_tool_name(tool)
        event_type = "hosted_tool.completed" if _is_hosted_sdk_tool(tool) else "tool.completed"
        self.trace.record(
            event_type,
            agent=getattr(agent, "name", str(agent)),
            tool=tool_name,
            result=_serializable(result),
        )

    async def on_llm_start(self, _context: Any, agent: Any, _system_prompt: str | None, input_items: list[Any]) -> None:
        self.trace.record("llm.started", agent=getattr(agent, "name", str(agent)), input_count=len(input_items))

    async def on_llm_end(self, _context: Any, agent: Any, _response: Any) -> None:
        self.trace.record("llm.completed", agent=getattr(agent, "name", str(agent)))


def build_openai_agent(
    manifest: AgentManifest,
    instructions: str,
    tools: list[Any] | None = None,
    handoffs: list[Any] | None = None,
    output_type: Any | None = None,
    hooks: Any | None = None,
    input_guardrails: list[Any] | None = None,
) -> Any:
    try:
        from agents import Agent
    except Exception as exc:  # noqa: BLE001 - optional adapter import boundary.
        raise OpenAIAdapterUnavailable("openai-agents is not installed") from exc
    kwargs: dict[str, Any] = {
        "name": manifest["agent"],
        "instructions": instructions,
        "model": manifest.get("model", "gpt-5.5"),
        "tools": tools or [],
        "handoffs": handoffs or [],
    }
    if output_type is not None:
        kwargs["output_type"] = output_type
    if hooks is not None:
        kwargs["hooks"] = hooks
    if input_guardrails is not None:
        kwargs["input_guardrails"] = input_guardrails
    return Agent(**kwargs)


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


async def run_openai_agent(
    agent: Any,
    user_input: str,
    *,
    context: Any | None = None,
    max_turns: int | None = 10,
    hooks: Any | None = None,
) -> OpenAIAdapterResult:
    try:
        from agents import Runner
    except Exception as exc:  # noqa: BLE001 - optional adapter import boundary.
        raise OpenAIAdapterUnavailable("openai-agents is not installed") from exc
    result = await Runner.run(agent, user_input, context=context, max_turns=max_turns, hooks=hooks)
    last_agent = getattr(getattr(result, "last_agent", None), "name", None)
    return OpenAIAdapterResult(getattr(result, "final_output", None), last_agent, result)


async def run_openai_agent_with_contract(
    agent: Any,
    user_input: str,
    *,
    contract: CompilerArtifacts,
    agent_name: str,
    trace: TraceRecorder | None = None,
    runtime_context: RuntimeContext | None = None,
    context: Any | None = None,
    hidden_truth: Mapping[str, Any] | None = None,
    approval_callback: ApprovalCallback | None = None,
    max_turns: int | None = 10,
    hooks: Any | None = None,
) -> OpenAIContractRunResult:
    """Run one OpenAI SDK agent and evaluate its Contract4Agents assertions.

    This helper does not choose a route, replay a workflow, or orchestrate a
    team. It runs the supplied SDK agent once, resolves SDK approval
    interruptions if a callback is supplied, then evaluates the compiled
    assertions for the named agent.
    """
    try:
        from agents import Runner
    except Exception as exc:  # noqa: BLE001 - optional adapter import boundary.
        raise OpenAIAdapterUnavailable("openai-agents is not installed") from exc
    run_trace = trace or TraceRecorder()
    run_hooks = hooks or OpenAITraceHooks(run_trace)
    prompt = _input_with_rendered_context(user_input, runtime_context)
    runner_context = context if context is not None else runtime_context
    result = await Runner.run(agent, prompt, context=runner_context, max_turns=max_turns, hooks=run_hooks)
    result, approvals = await _resolve_approval_interruptions(
        Runner,
        agent,
        result,
        runner_context,
        max_turns,
        run_hooks,
        run_trace,
        approval_callback,
    )
    final_output = _serializable(getattr(result, "final_output", None))
    assertion_result = evaluate_run_contract(
        contract=contract,
        trace=run_trace,
        outputs={agent_name: final_output},
        target_agents=[agent_name],
        context=runtime_context.values if runtime_context is not None else None,
        hidden_truth=hidden_truth,
    )
    _record_assertion_events(run_trace, assertion_result)
    last_agent = getattr(getattr(result, "last_agent", None), "name", None)
    return OpenAIContractRunResult(
        OpenAIAdapterResult(final_output, last_agent, result),
        assertion_result,
        run_trace,
        approvals,
    )


class OpenAISemanticJudge:
    def __init__(self, model: str = "gpt-5.5", api_key_env: str = "OPENAI_API_KEY") -> None:
        self.model = model
        self.api_key_env = api_key_env

    async def judge(self, *, output: dict[str, Any], criterion: str) -> bool:
        if not os.getenv(self.api_key_env):
            raise OpenAIAdapterUnavailable(f"{self.api_key_env} is not set")
        try:
            from openai import AsyncOpenAI
        except Exception as exc:  # noqa: BLE001 - optional adapter import boundary.
            raise OpenAIAdapterUnavailable("openai package is not installed") from exc
        client = AsyncOpenAI()
        response = await client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": "Return only PASS or FAIL. Evaluate whether the output satisfies the criterion.",
                },
                {
                    "role": "user",
                    "content": f"Criterion: {criterion}\nOutput: {output}",
                },
            ],
        )
        text = getattr(response, "output_text", "")
        return str(text).strip().upper() == "PASS"


def _serializable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict | list | str | int | float | bool) or value is None:
        return value
    return str(value)


def _normalized_tool_name(tool: Any) -> str:
    if _is_hosted_sdk_tool(tool):
        return "openai.web_search"
    return contract_tool_name(getattr(tool, "name", str(tool)))


def _is_hosted_sdk_tool(tool: Any) -> bool:
    return str(tool.__class__.__name__) == "WebSearchTool"


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
    if callable(registry_entry) and not _looks_like_sdk_tool(registry_entry):
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


def _looks_like_sdk_tool(entry: Any) -> bool:
    class_name = entry.__class__.__name__
    return hasattr(entry, "name") or class_name in {"FunctionTool", "WebSearchTool"}


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
                tool=_hosted_tool_from_registry(name, hosted_tool["config"], hosted_tool_registry[name]),
            )
        )
    return tools


def _hosted_tool_from_registry(name: str, config: dict[str, str], registry_entry: Any) -> Any:
    kwargs = hosted_tool_kwargs(name, config)
    if registry_entry is True:
        if name == "openai.web_search":
            try:
                from agents import WebSearchTool
            except Exception as exc:  # noqa: BLE001 - optional adapter import boundary.
                raise OpenAIAdapterUnavailable("openai-agents is not installed") from exc
            return WebSearchTool(search_context_size=cast(Any, kwargs["search_context_size"]))
        raise OpenAIAgentFactoryError(f"No built-in OpenAI hosted tool mapping for `{name}`")
    if callable(registry_entry):
        return registry_entry(**kwargs)
    return registry_entry


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
        if declared_mode in {"agent_as_tool", "as_tool"}:
            plans.append(_agent_tool_composition(agent_name, child, agent_tool_registry, caveats, declared_mode))
            continue
        if declared_mode == "handoff":
            plans.append(_handoff_composition(agent_name, child, handoff_registry, caveats, "handoff"))
            continue
        has_agent_tool = bool(agent_tool_registry and child in agent_tool_registry)
        has_handoff = bool(handoff_registry and child in handoff_registry)
        if has_agent_tool:
            if has_handoff:
                caveats.append(
                    OpenAIAgentFactoryCaveat(
                        agent_name,
                        "composition_mode_ambiguous",
                        f"Agent dependency `{child}` has both agent-tool and handoff registrations; "
                        "agent-tool was used.",
                    )
                )
            plans.append(_agent_tool_composition(agent_name, child, agent_tool_registry, caveats, "implicit"))
        elif has_handoff:
            plans.append(_handoff_composition(agent_name, child, handoff_registry, caveats, "implicit"))
        else:
            caveats.append(
                OpenAIAgentFactoryCaveat(
                    agent_name,
                    "agent_dependency_unwired",
                    f"Declared agent dependency `{child}` was not supplied as an agent tool or handoff.",
                )
            )
            plans.append(OpenAICompositionPlan(agent_name, child, "unwired"))
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


def _input_with_rendered_context(user_input: str, runtime_context: RuntimeContext | None) -> str:
    if runtime_context is None:
        return user_input
    rendered = runtime_context.rendered_context()
    if not rendered:
        return user_input
    return f"{user_input}\n\nResolved context:\n{rendered}"


async def _resolve_approval_interruptions(
    runner: Any,
    agent: Any,
    result: Any,
    context: Any,
    max_turns: int | None,
    hooks: Any,
    trace: TraceRecorder,
    approval_callback: ApprovalCallback | None,
) -> tuple[Any, list[OpenAIApprovalRequest]]:
    approvals: list[OpenAIApprovalRequest] = []
    loops = 0
    while getattr(result, "interruptions", None):
        loops += 1
        if loops > 10:
            raise OpenAIAgentFactoryError("Too many OpenAI approval interruption loops")
        if approval_callback is None:
            raise OpenAIAgentFactoryError("OpenAI approval interruption requires an approval_callback")
        state = result.to_state()
        for interruption in result.interruptions:
            tool_name = contract_tool_name(str(getattr(interruption, "tool_name", "")))
            arguments = _approval_arguments(interruption)
            trace.record("approval.requested", tool=tool_name, arguments=arguments)
            approved = bool(await _maybe_await(approval_callback(OpenAIApprovalRequest(tool_name, None, arguments))))
            trace.record("approval.completed", tool=tool_name, approved=approved)
            approvals.append(OpenAIApprovalRequest(tool_name, approved, arguments))
            if approved:
                state.approve(interruption)
            else:
                state.reject(interruption, rejection_message=f"Approval denied for {tool_name}")
        result = await runner.run(agent, state, context=context, max_turns=max_turns, hooks=hooks)
    return result, approvals


def _approval_arguments(interruption: Any) -> dict[str, Any]:
    for attr in ("arguments", "tool_arguments", "input"):
        value = getattr(interruption, attr, None)
        if isinstance(value, dict):
            return dict(value)
    return {}


async def _maybe_await(value: bool | Awaitable[bool]) -> bool:
    if inspect.isawaitable(value):
        return bool(await value)
    return bool(value)


def _record_assertion_events(trace: TraceRecorder, result: RunEvaluationResult) -> None:
    for agent_result in result.agents:
        for check in agent_result.checks:
            data: dict[str, Any] = {"status": check.status}
            if check.failure is not None:
                data["failure_kind"] = check.failure.kind
                data["message"] = check.failure.message
            trace.record("assertion.evaluated", agent=agent_result.agent, assertion=check.assertion, data=data)


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
