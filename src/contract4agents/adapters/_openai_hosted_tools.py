"""OpenAI hosted-tool construction helpers."""

from __future__ import annotations

from typing import Any, cast

from contract4agents.adapters._openai_types import OpenAIAdapterUnavailable, OpenAIAgentFactoryError


def looks_like_sdk_tool(entry: Any) -> bool:
    class_name = entry.__class__.__name__
    return hasattr(entry, "name") or class_name in {"FunctionTool", "WebSearchTool"}


def hosted_tool_from_registry(name: str, config: dict[str, str], registry_entry: Any) -> Any:
    kwargs = _openai_hosted_tool_kwargs(name, config)
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


def _openai_hosted_tool_kwargs(name: str, config: dict[str, str]) -> dict[str, object]:
    if name == "openai.web_search":
        return {"search_context_size": config.get("context_size", "medium")}
    return dict(config)


__all__ = ["hosted_tool_from_registry", "looks_like_sdk_tool"]
