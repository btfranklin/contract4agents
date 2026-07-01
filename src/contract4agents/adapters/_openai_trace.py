"""OpenAI Agents SDK trace hook helpers."""

from __future__ import annotations

from typing import Any

from contract4agents.adapters._openai_names import contract_tool_name
from contract4agents.runtime import TraceRecorder

_RunHooksBase: type[Any]
try:
    from agents import RunHooks as _ImportedRunHooks

    _RunHooksBase = _ImportedRunHooks
except Exception:  # noqa: BLE001 - optional adapter import boundary.
    _RunHooksBase = object


class OpenAITraceHooks(_RunHooksBase):  # type: ignore[misc]
    """Minimal hook object that normalizes Agents SDK lifecycle events to Contract4Agents traces."""

    def __init__(self, trace: TraceRecorder) -> None:
        super().__init__()
        self.trace = trace

    async def on_agent_start(self, _context: Any, agent: Any) -> None:
        self.trace.record("agent.started", agent=getattr(agent, "name", str(agent)))

    async def on_agent_end(self, _context: Any, agent: Any, output: Any) -> None:
        self.trace.record("agent.completed", agent=getattr(agent, "name", str(agent)), output=serializable(output))

    async def on_handoff(self, _context: Any, from_agent: Any, to_agent: Any) -> None:
        self.trace.record(
            "agent.handoff",
            from_agent=getattr(from_agent, "name", str(from_agent)),
            to_agent=getattr(to_agent, "name", str(to_agent)),
        )

    async def on_tool_start(self, _context: Any, agent: Any, tool: Any) -> None:
        tool_name = normalized_tool_name(tool)
        event_type = "hosted_tool.started" if is_hosted_sdk_tool(tool) else "tool.started"
        self.trace.record(
            event_type,
            agent=getattr(agent, "name", str(agent)),
            tool=tool_name,
        )

    async def on_tool_end(self, _context: Any, agent: Any, tool: Any, result: str) -> None:
        tool_name = normalized_tool_name(tool)
        event_type = "hosted_tool.completed" if is_hosted_sdk_tool(tool) else "tool.completed"
        self.trace.record(
            event_type,
            agent=getattr(agent, "name", str(agent)),
            tool=tool_name,
            result=serializable(result),
        )

    async def on_llm_start(self, _context: Any, agent: Any, _system_prompt: str | None, input_items: list[Any]) -> None:
        self.trace.record("llm.started", agent=getattr(agent, "name", str(agent)), input_count=len(input_items))

    async def on_llm_end(self, _context: Any, agent: Any, _response: Any) -> None:
        self.trace.record("llm.completed", agent=getattr(agent, "name", str(agent)))


def serializable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict | list | str | int | float | bool) or value is None:
        return value
    return str(value)


def normalized_tool_name(tool: Any) -> str:
    if is_hosted_sdk_tool(tool):
        return "openai.web_search"
    return contract_tool_name(getattr(tool, "name", str(tool)))


def is_hosted_sdk_tool(tool: Any) -> bool:
    return str(tool.__class__.__name__) == "WebSearchTool"


__all__ = ["OpenAITraceHooks", "is_hosted_sdk_tool", "normalized_tool_name", "serializable"]
