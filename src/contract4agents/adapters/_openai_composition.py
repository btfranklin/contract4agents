"""OpenAI adapter composition planning helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from contract4agents.adapters._openai_types import OpenAIAgentFactoryCaveat, OpenAICompositionPlan
from contract4agents.compiler import AgentManifest
from contract4agents.composition import parse_composition_declaration


def planned_composition(
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


__all__ = ["planned_composition"]
