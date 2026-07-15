"""V2 agent signature and attribute checks."""

from __future__ import annotations

from contract4agents.ast import AgentDef
from contract4agents.diagnostics import Diagnostic
from contract4agents.semantic_checks._index import ProjectIndex
from contract4agents.semantic_checks._types import check_type_ref

TEXT_AGENT_ATTRIBUTES = {"description", "goal"}
LIST_AGENT_ATTRIBUTES = {"guidance"}
AGENT_ATTRIBUTES = TEXT_AGENT_ATTRIBUTES | LIST_AGENT_ATTRIBUTES
COMMON_AGENT_ATTRIBUTE_MISSPELLINGS: dict[str, str] = {}


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
            hint = _unknown_agent_attribute_hint(key)
            diagnostics.append(
                Diagnostic(
                    "SEM070",
                    f"Unknown agent attribute `{key}` on `{agent.name}`",
                    span=span,
                    hint=hint,
                )
            )
            continue
        if key in TEXT_AGENT_ATTRIBUTES and not isinstance(value, str):
            diagnostics.append(
                Diagnostic(
                    "SEM071",
                    f"Agent attribute `{key}` on `{agent.name}` must be a string",
                    span=span,
                )
            )
        elif key in LIST_AGENT_ATTRIBUTES and not isinstance(value, list):
            diagnostics.append(
                Diagnostic(
                    "SEM071",
                    f"Agent attribute `{key}` on `{agent.name}` must be a list",
                    span=span,
                )
            )
    return diagnostics


def _unknown_agent_attribute_hint(key: str) -> str:
    expected = COMMON_AGENT_ATTRIBUTE_MISSPELLINGS.get(key)
    if expected:
        return f"Use `{expected}`."
    return "Accepted agent attributes are: " + ", ".join(f"`{item}`" for item in sorted(AGENT_ATTRIBUTES)) + "."


__all__ = ["check_agent"]
