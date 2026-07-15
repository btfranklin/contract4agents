"""Deterministic serialization for validated target bindings."""

from __future__ import annotations

import json
from collections.abc import Mapping

from contract4agents.target_bindings._models import (
    AgentProfile,
    BindingEntry,
    TargetBinding,
    TargetBindings,
    TargetProfile,
)


def target_bindings_dict(bindings: TargetBindings) -> dict[str, object]:
    """Return a deterministic plain-data representation without the local file path."""

    return {
        "schema_version": bindings.schema_version,
        "targets": {name: _target_dict(bindings.targets[name]) for name in sorted(bindings.targets)},
    }


def canonical_target_bindings_json(bindings: TargetBindings) -> str:
    """Serialize bindings with the same stable JSON rules used by future digests."""

    return json.dumps(target_bindings_dict(bindings), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _target_dict(target: TargetBinding) -> dict[str, object]:
    result: dict[str, object] = {"adapter": target.adapter}
    for name, values in (
        ("tools", target.tools),
        ("datasources", target.datasources),
        ("external_context", target.external_context),
        ("environments", target.environments),
    ):
        if values:
            result[name] = {key: _entry_dict(values[key]) for key in sorted(values)}
    if target.profiles:
        result["profiles"] = {name: _profile_dict(target.profiles[name]) for name in sorted(target.profiles)}
    return result


def _entry_dict(entry: BindingEntry) -> dict[str, object]:
    return {name: _thaw(entry.values[name]) for name in sorted(entry.values)}


def _profile_dict(profile: TargetProfile) -> dict[str, object]:
    result: dict[str, object] = {}
    if profile.default_model is not None:
        result["default_model"] = profile.default_model
    if profile.agents:
        result["agents"] = {name: _agent_profile_dict(profile.agents[name]) for name in sorted(profile.agents)}
    if profile.options:
        result["options"] = {name: _thaw(profile.options[name]) for name in sorted(profile.options)}
    return result


def _agent_profile_dict(profile: AgentProfile) -> dict[str, object]:
    result: dict[str, object] = {}
    if profile.model is not None:
        result["model"] = profile.model
    if profile.options:
        result["options"] = {name: _thaw(profile.options[name]) for name in sorted(profile.options)}
    return result


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(name): _thaw(item) for name, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


__all__ = ["canonical_target_bindings_json", "target_bindings_dict"]
