"""Hosted provider tool metadata supported by Contract4Agents V1."""

from __future__ import annotations

from typing import Final

HOSTED_TOOL_CONTEXT_SIZES: Final = frozenset({"low", "medium", "high"})

SUPPORTED_HOSTED_TOOLS: Final = {
    "openai": {
        "web_search": {
            "context_size": HOSTED_TOOL_CONTEXT_SIZES,
        }
    }
}


def split_hosted_tool_name(name: str) -> tuple[str, str] | None:
    parts = name.split(".", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def hosted_tool_kwargs(name: str, config: dict[str, str]) -> dict[str, object]:
    if name == "openai.web_search":
        return {"search_context_size": config.get("context_size", "medium")}
    return dict(config)


__all__ = [
    "HOSTED_TOOL_CONTEXT_SIZES",
    "SUPPORTED_HOSTED_TOOLS",
    "hosted_tool_kwargs",
    "split_hosted_tool_name",
]
