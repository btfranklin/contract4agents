"""Provider-hosted tool descriptor registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

HOSTED_TOOL_CONTEXT_SIZES: Final = frozenset({"low", "medium", "high"})

HostedToolOptions = Mapping[str, frozenset[str]]


@dataclass(frozen=True)
class HostedToolDescriptor:
    provider: str
    tools: Mapping[str, HostedToolOptions]

    def tool_options(self, tool: str) -> HostedToolOptions | None:
        return self.tools.get(tool)


OPENAI_HOSTED_TOOL_DESCRIPTOR: Final = HostedToolDescriptor(
    provider="openai",
    tools={
        "web_search": {
            "context_size": HOSTED_TOOL_CONTEXT_SIZES,
        },
    },
)

HOSTED_TOOL_DESCRIPTORS: Final = {
    OPENAI_HOSTED_TOOL_DESCRIPTOR.provider: OPENAI_HOSTED_TOOL_DESCRIPTOR,
}


def hosted_tool_descriptor(provider: str) -> HostedToolDescriptor | None:
    return HOSTED_TOOL_DESCRIPTORS.get(provider)


def register_hosted_tool_descriptor(descriptor: HostedToolDescriptor) -> None:
    HOSTED_TOOL_DESCRIPTORS[descriptor.provider] = descriptor


def hosted_tool_descriptors() -> Mapping[str, HostedToolDescriptor]:
    return HOSTED_TOOL_DESCRIPTORS


def split_hosted_tool_name(name: str) -> tuple[str, str] | None:
    parts = name.split(".", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


__all__ = [
    "HOSTED_TOOL_CONTEXT_SIZES",
    "HOSTED_TOOL_DESCRIPTORS",
    "HostedToolDescriptor",
    "hosted_tool_descriptor",
    "hosted_tool_descriptors",
    "register_hosted_tool_descriptor",
    "split_hosted_tool_name",
]
