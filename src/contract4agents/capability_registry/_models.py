"""Capability registry data models and constants."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contract4agents.diagnostics import Diagnostic

DEFAULT_REGISTRY_FILENAME = "contract4agents.registry.json"
REGISTRY_VERSION = 2
SECTIONS = ("tools", "hosted_tools", "agents", "output_types", "prompts", "host_context")
PERMISSIONS = {"available", "preapproved", "requires_approval", "denied", "sandboxed"}


@dataclass(frozen=True)
class CapabilityRegistry:
    """Validated capability registry content."""

    path: Path
    tools: Mapping[str, Mapping[str, Any]]
    hosted_tools: Mapping[str, Mapping[str, Any]]
    agents: Mapping[str, Mapping[str, Any]]
    output_types: Mapping[str, Mapping[str, Any]]
    prompts: Mapping[str, Mapping[str, Any]]
    host_context: Mapping[str, list[str]]


@dataclass(frozen=True)
class CapabilityRegistryLoad:
    """Registry load result with diagnostics instead of exceptions."""

    path: Path
    registry: CapabilityRegistry | None
    diagnostics: list[Diagnostic]


__all__ = [
    "CapabilityRegistry",
    "CapabilityRegistryLoad",
    "DEFAULT_REGISTRY_FILENAME",
    "PERMISSIONS",
    "REGISTRY_VERSION",
    "SECTIONS",
]
