"""Agent signature and attribute checks."""

from __future__ import annotations

from contract4agents.ast import AgentDef
from contract4agents.diagnostics import Diagnostic
from contract4agents.language_spec import AGENT_ATTRIBUTES, AGENT_LIST_ATTRIBUTES, AGENT_TEXT_ATTRIBUTES
from contract4agents.semantic_checks._index import ProjectIndex
from contract4agents.semantic_checks._types import check_type_ref


def check_agent(
    agent: AgentDef,
    index: ProjectIndex,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_check_agent_attributes(agent))
    for parameter in agent.parameters:
        diagnostics.extend(check_type_ref(parameter.type_name, index, parameter.span, "agent parameter"))
    diagnostics.extend(check_type_ref(agent.return_type, index, agent.span, "agent return type"))
    return diagnostics


def _check_agent_attributes(agent: AgentDef) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for key, value in agent.attributes.items():
        span = agent.attribute_spans.get(key, agent.span)
        if key not in AGENT_ATTRIBUTES:
            diagnostics.append(
                Diagnostic(
                    "SEM070",
                    f"Unknown agent attribute `{key}` on `{agent.name}`",
                    span=span,
                    hint=(
                        "Accepted agent attributes are: "
                        + ", ".join(f"`{item}`" for item in sorted(AGENT_ATTRIBUTES))
                        + "."
                    ),
                )
            )
            continue
        if key in AGENT_TEXT_ATTRIBUTES and not isinstance(value, str):
            diagnostics.append(
                Diagnostic(
                    "SEM071",
                    f"Agent attribute `{key}` on `{agent.name}` must be a string",
                    span=span,
                )
            )
        elif key in AGENT_LIST_ATTRIBUTES and not isinstance(value, list):
            diagnostics.append(
                Diagnostic(
                    "SEM071",
                    f"Agent attribute `{key}` on `{agent.name}` must be a list",
                    span=span,
                )
            )
    return diagnostics

__all__ = ["check_agent"]
