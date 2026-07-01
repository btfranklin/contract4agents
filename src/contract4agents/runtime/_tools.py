"""Fake tool registry internals for Contract4Agents."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from contract4agents.runtime._errors import ToolExecutionFailed, ToolPermissionDenied
from contract4agents.runtime._trace import TraceRecorder

ToolCallable = Callable[..., Any]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    func: ToolCallable
    permission: str = "preapproved"


class FakeToolRegistry:
    def __init__(self, approval_callback: Callable[[str, dict[str, Any]], bool] | None = None) -> None:
        self.tools: dict[str, ToolSpec] = {}
        self.approval_callback = approval_callback

    def register(self, name: str, func: ToolCallable, permission: str = "preapproved") -> None:
        self.tools[name] = ToolSpec(name, func, permission)

    async def call(self, name: str, trace: TraceRecorder, **kwargs: Any) -> Any:
        if name not in self.tools:
            trace.record("tool.failed", tool=name, reason="tool is not registered")
            raise ToolExecutionFailed(name, "tool is not registered")
        spec = self.tools[name]
        trace.record("tool.requested", tool=name, arguments=kwargs)
        if spec.permission == "denied":
            trace.record("tool.denied", tool=name, reason="permission denied")
            raise ToolPermissionDenied(name)
        if spec.permission == "requires_approval":
            trace.record("approval.requested", tool=name, arguments=kwargs)
            allowed = bool(self.approval_callback and self.approval_callback(name, kwargs))
            trace.record("approval.completed", tool=name, approved=allowed)
            if not allowed:
                trace.record("tool.denied", tool=name, reason="approval denied")
                raise ToolPermissionDenied(name)
        trace.record("tool.allowed", tool=name)
        try:
            result = spec.func(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            trace.record("tool.completed", tool=name, result=result)
        except Exception as exc:
            trace.record("tool.failed", tool=name, reason=str(exc))
            raise ToolExecutionFailed(name, str(exc)) from exc
        return result


__all__ = ["FakeToolRegistry", "ToolCallable", "ToolSpec"]
