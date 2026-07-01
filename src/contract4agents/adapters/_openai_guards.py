"""OpenAI adapter guard-plan caveat helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from contract4agents.adapters._openai_types import OpenAIAgentFactoryCaveat, OpenAIAgentFactoryError
from contract4agents.compiler import AgentManifest
from contract4agents.guards import GuardPlanItem


def guard_plan_by_agent(guard_plan: list[GuardPlanItem]) -> dict[str, list[GuardPlanItem]]:
    result: dict[str, list[GuardPlanItem]] = {}
    for item in guard_plan:
        result.setdefault(item["agent"], []).append(item)
    return result


def validate_output_guards(
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


def collect_guard_caveats(
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


__all__ = ["collect_guard_caveats", "guard_plan_by_agent", "validate_output_guards"]
