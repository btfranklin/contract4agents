"""OpenAI Agents SDK object construction helpers."""

from __future__ import annotations

from typing import Any

from contract4agents.adapters._openai_types import OpenAIAdapterUnavailable
from contract4agents.compiler import AgentManifest


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


__all__ = ["build_openai_agent"]
