"""Immutable domain models for target-specific implementation bindings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TypeVar

from contract4agents.diagnostics import Diagnostic

DEFAULT_TARGET_BINDINGS_FILENAME = "contract4agents.targets.toml"
TARGET_BINDINGS_SCHEMA_VERSION = "2"


@dataclass(frozen=True)
class BindingEntry:
    """Adapter-owned implementation locator and options for one semantic ID."""

    values: dict[str, object] | MappingProxyType[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", _freeze_mapping(dict(self.values)))


@dataclass(frozen=True)
class AgentProfile:
    """Model selection and provider options for one agent in one profile."""

    model: str | None = None
    options: dict[str, object] | MappingProxyType[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", _freeze_mapping(dict(self.options)))


@dataclass(frozen=True)
class TargetProfile:
    """A complete, non-inheriting target profile."""

    default_model: str | None = None
    agents: dict[str, AgentProfile] | MappingProxyType[str, AgentProfile] = field(default_factory=dict)
    options: dict[str, object] | MappingProxyType[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "agents", _freeze_typed_mapping(dict(self.agents)))
        object.__setattr__(self, "options", _freeze_mapping(dict(self.options)))


@dataclass(frozen=True)
class TargetBinding:
    """All implementation bindings and profiles for one adapter target."""

    adapter: str
    tools: dict[str, BindingEntry] | MappingProxyType[str, BindingEntry] = field(default_factory=dict)
    datasources: dict[str, BindingEntry] | MappingProxyType[str, BindingEntry] = field(default_factory=dict)
    external_context: dict[str, BindingEntry] | MappingProxyType[str, BindingEntry] = field(default_factory=dict)
    environments: dict[str, BindingEntry] | MappingProxyType[str, BindingEntry] = field(default_factory=dict)
    profiles: dict[str, TargetProfile] | MappingProxyType[str, TargetProfile] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.profiles:
            raise ValueError("A target binding must declare at least one named profile")
        object.__setattr__(self, "tools", _freeze_typed_mapping(dict(self.tools)))
        object.__setattr__(self, "datasources", _freeze_typed_mapping(dict(self.datasources)))
        object.__setattr__(self, "external_context", _freeze_typed_mapping(dict(self.external_context)))
        object.__setattr__(self, "environments", _freeze_typed_mapping(dict(self.environments)))
        object.__setattr__(self, "profiles", _freeze_typed_mapping(dict(self.profiles)))


@dataclass(frozen=True)
class TargetBindings:
    """Validated contents of one target-binding document."""

    path: Path
    targets: dict[str, TargetBinding] | MappingProxyType[str, TargetBinding]
    schema_version: str = TARGET_BINDINGS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "targets", _freeze_typed_mapping(dict(self.targets)))


@dataclass(frozen=True)
class TargetBindingsLoad:
    """A load result that preserves structured diagnostics instead of raising."""

    path: Path
    bindings: TargetBindings | None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        return self.bindings is not None and not any(item.severity == "error" for item in self.diagnostics)


_T = TypeVar("_T")


def _freeze_typed_mapping(values: dict[str, _T]) -> MappingProxyType[str, _T]:
    return MappingProxyType({name: values[name] for name in sorted(values)})


def _freeze_mapping(values: dict[str, object]) -> MappingProxyType[str, object]:
    return MappingProxyType({name: _freeze_value(values[name]) for name in sorted(values)})


def _freeze_value(value: object) -> object:
    if isinstance(value, dict):
        return _freeze_mapping({str(name): item for name, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(item) for item in value)
    return value


__all__ = [
    "AgentProfile",
    "BindingEntry",
    "DEFAULT_TARGET_BINDINGS_FILENAME",
    "TARGET_BINDINGS_SCHEMA_VERSION",
    "TargetBinding",
    "TargetBindings",
    "TargetBindingsLoad",
    "TargetProfile",
]
