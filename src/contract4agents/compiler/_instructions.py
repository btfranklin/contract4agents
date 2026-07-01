"""Model-facing instruction generation."""

from __future__ import annotations

from contract4agents.ast import AgentDef


def agent_instructions(agent: AgentDef) -> str:
    lines = [f"# {agent.name}", "", agent.text_attr("goal")]
    description = agent.text_attr("description")
    if description:
        lines.extend(["", f"Description: {description}"])
    for label, key in [
        ("Policy", "policy"),
        ("Success Criteria", "success"),
        ("Routes", "routes"),
        ("Guards", "guards"),
        ("Assertions", "assertions"),
    ]:
        items = agent.list_attr(key)
        if items:
            lines.extend(["", f"## {label}"])
            lines.extend(f"- {item}" for item in items)
    lines.extend(["", f"Return output conforming to `{agent.return_type}`."])
    return "\n".join(lines).strip() + "\n"


__all__ = ["agent_instructions"]
