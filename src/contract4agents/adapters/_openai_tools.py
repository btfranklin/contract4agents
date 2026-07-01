"""OpenAI adapter tool planning helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from contract4agents.adapters._openai_hosted_tools import hosted_tool_from_registry, looks_like_sdk_tool
from contract4agents.adapters._openai_names import openai_tool_name
from contract4agents.adapters._openai_types import (
    OpenAIAdapterUnavailable,
    OpenAIAgentFactoryCaveat,
    OpenAIAgentFactoryError,
    OpenAIHostedToolPlan,
    OpenAIToolPlan,
    OpenAIToolRegistration,
)
from contract4agents.compiler import AgentManifest
from contract4agents.guards import GuardPlanItem


def planned_tools(
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


def planned_hosted_tools(
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


__all__ = ["planned_hosted_tools", "planned_tools"]
