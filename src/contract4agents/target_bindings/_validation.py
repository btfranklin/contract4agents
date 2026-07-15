"""Structural and authority-boundary validation for target bindings."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from contract4agents.diagnostics import Diagnostic
from contract4agents.target_bindings._models import TARGET_BINDINGS_SCHEMA_VERSION

_TOP_LEVEL_KEYS = frozenset({"schema_version", "targets"})
_TARGET_KEYS = frozenset(
    {"adapter", "tools", "datasources", "external_context", "environments", "profiles"}
)
_PROFILE_KEYS = frozenset({"default_model", "agents", "options"})
_AGENT_PROFILE_KEYS = frozenset({"model", "options"})
_PROFILE_INHERITANCE_KEYS = frozenset({"base_profile", "extends", "inherits", "parent"})
_CONTRACT_OWNED_KEYS = frozenset(
    {
        "audience",
        "assessment",
        "authorization",
        "availability",
        "composition",
        "control",
        "controls",
        "execution",
        "goal",
        "guidance",
        "isolation",
        "operational_control",
        "operational_controls",
        "qualities",
        "quality",
        "rubric",
    }
)


def validate_target_bindings_payload(payload: object, path: Path) -> list[Diagnostic]:
    """Return deterministic diagnostics for a decoded TOML document."""

    if not isinstance(payload, dict):
        return [_invalid(f"Target bindings `{path}` must be a TOML table")]

    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_forbidden_keys(payload, "target-binding document"))
    diagnostics.extend(_unknown_keys(payload, _TOP_LEVEL_KEYS, "target-binding document"))
    if payload.get("schema_version") != TARGET_BINDINGS_SCHEMA_VERSION:
        diagnostics.append(
            _invalid(
                f"Target bindings `{path}` must declare "
                f'`schema_version = "{TARGET_BINDINGS_SCHEMA_VERSION}"`'
            )
        )

    targets = payload.get("targets")
    if not isinstance(targets, dict) or not targets:
        diagnostics.append(_invalid("Target bindings must declare a non-empty `targets` table"))
        return diagnostics

    for target_name in sorted(targets):
        target = targets[target_name]
        target_path = f"targets.{target_name}"
        if not isinstance(target, dict):
            diagnostics.append(_invalid(f"`{target_path}` must be a table"))
            continue
        diagnostics.extend(_forbidden_keys(target, target_path))
        diagnostics.extend(_unknown_keys(target, _TARGET_KEYS, target_path))
        diagnostics.extend(_validate_target(target, target_path))
    return diagnostics


def _validate_target(target: dict[str, Any], path: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    adapter = target.get("adapter")
    if not isinstance(adapter, str) or not adapter.strip():
        diagnostics.append(_invalid(f"`{path}.adapter` must be a non-empty string"))

    for section_name in ("tools", "datasources", "external_context"):
        diagnostics.extend(_validate_binding_section(target.get(section_name, {}), f"{path}.{section_name}"))
    diagnostics.extend(_validate_environments(target.get("environments", {}), f"{path}.environments"))
    diagnostics.extend(_validate_profiles(target.get("profiles", {}), f"{path}.profiles"))
    return diagnostics


def _validate_binding_section(value: object, path: str) -> list[Diagnostic]:
    if not isinstance(value, dict):
        return [_invalid(f"`{path}` must be a table")]
    diagnostics: list[Diagnostic] = []
    for name in sorted(value):
        entry = value[name]
        entry_path = f"{path}.{name}"
        if not isinstance(entry, dict) or not entry:
            diagnostics.append(_invalid(f"`{entry_path}` must be a non-empty table"))
            continue
        diagnostics.extend(_validate_values(entry, entry_path))
    return diagnostics


def _validate_environments(value: object, path: str) -> list[Diagnostic]:
    diagnostics = _validate_binding_section(value, path)
    if not isinstance(value, dict):
        return diagnostics
    for name in sorted(value):
        entry = value[name]
        if not isinstance(entry, dict) or not entry:
            continue
        provider = entry.get("provider")
        if not isinstance(provider, str) or not provider.strip():
            diagnostics.append(_invalid(f"`{path}.{name}.provider` must be a non-empty string"))
    return diagnostics


def _validate_profiles(value: object, path: str) -> list[Diagnostic]:
    if not isinstance(value, dict):
        return [_invalid(f"`{path}` must be a table")]
    diagnostics: list[Diagnostic] = []
    for name in sorted(value):
        profile = value[name]
        profile_path = f"{path}.{name}"
        if not isinstance(profile, dict):
            diagnostics.append(_invalid(f"`{profile_path}` must be a table"))
            continue
        diagnostics.extend(_forbidden_keys(profile, profile_path))
        diagnostics.extend(_profile_inheritance(profile, profile_path))
        diagnostics.extend(_unknown_keys(profile, _PROFILE_KEYS, profile_path))

        default_model = profile.get("default_model")
        if default_model is not None and (not isinstance(default_model, str) or not default_model.strip()):
            diagnostics.append(_invalid(f"`{profile_path}.default_model` must be a non-empty string"))
        diagnostics.extend(_validate_values(profile.get("options", {}), f"{profile_path}.options"))
        diagnostics.extend(_validate_agent_profiles(profile.get("agents", {}), f"{profile_path}.agents"))
    return diagnostics


def _validate_agent_profiles(value: object, path: str) -> list[Diagnostic]:
    if not isinstance(value, dict):
        return [_invalid(f"`{path}` must be a table")]
    diagnostics: list[Diagnostic] = []
    for name in sorted(value):
        profile = value[name]
        profile_path = f"{path}.{name}"
        if not isinstance(profile, dict):
            diagnostics.append(_invalid(f"`{profile_path}` must be a table"))
            continue
        diagnostics.extend(_forbidden_keys(profile, profile_path))
        diagnostics.extend(_profile_inheritance(profile, profile_path))
        diagnostics.extend(_unknown_keys(profile, _AGENT_PROFILE_KEYS, profile_path))
        model = profile.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            diagnostics.append(_invalid(f"`{profile_path}.model` must be a non-empty string"))
        diagnostics.extend(_validate_values(profile.get("options", {}), f"{profile_path}.options"))
    return diagnostics


def _validate_values(value: object, path: str) -> list[Diagnostic]:
    if not isinstance(value, dict):
        return [_invalid(f"`{path}` must be a table")]
    diagnostics = _forbidden_keys(value, path)
    for key in sorted(value):
        child = value[key]
        child_path = f"{path}.{key}"
        if isinstance(child, dict):
            diagnostics.extend(_validate_values(child, child_path))
        elif isinstance(child, list):
            diagnostics.extend(_validate_list(child, child_path))
        elif not isinstance(child, str | int | float | bool):
            diagnostics.append(
                _invalid(f"`{child_path}` must be a string, integer, float, boolean, list, or table")
            )
    return diagnostics


def _validate_list(value: list[object], path: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if isinstance(item, dict):
            diagnostics.extend(_validate_values(item, item_path))
        elif isinstance(item, list):
            diagnostics.extend(_validate_list(item, item_path))
        elif not isinstance(item, str | int | float | bool):
            diagnostics.append(
                _invalid(f"`{item_path}` must be a string, integer, float, boolean, list, or table")
            )
    return diagnostics


def _forbidden_keys(value: Mapping[str, object], path: str) -> list[Diagnostic]:
    return [
        Diagnostic(
            "TGT004",
            f"Target binding `{path}.{key}` duplicates contract-owned semantics",
            hint=(
                "Move authorization, goals, guidance, controls, qualities, audiences, and isolation "
                "requirements into `.contract` source."
            ),
        )
        for key in sorted(value)
        if key in _CONTRACT_OWNED_KEYS
    ]


def _profile_inheritance(value: Mapping[str, object], path: str) -> list[Diagnostic]:
    return [
        Diagnostic(
            "TGT005",
            f"Target profile `{path}` cannot declare inheritance key `{key}`",
            hint="Profiles are complete and do not inherit in target-binding schema version 1.",
        )
        for key in sorted(value)
        if key in _PROFILE_INHERITANCE_KEYS
    ]


def _unknown_keys(value: Mapping[str, object], allowed: frozenset[str], path: str) -> list[Diagnostic]:
    return [
        Diagnostic("TGT003", f"Unknown key `{key}` in `{path}`")
        for key in sorted(value)
        if key not in allowed and key not in _CONTRACT_OWNED_KEYS and key not in _PROFILE_INHERITANCE_KEYS
    ]


def _invalid(message: str) -> Diagnostic:
    return Diagnostic("TGT001", message)


__all__ = ["validate_target_bindings_payload"]
