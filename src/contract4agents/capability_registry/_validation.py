"""Shape validation for capability registry payloads."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from contract4agents.capability_registry._models import PERMISSIONS, REGISTRY_VERSION, SECTIONS
from contract4agents.diagnostics import Diagnostic

_EntryValidator = Callable[[str, str, Any], list[Diagnostic]]


def validate_payload(payload: Any, path: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if not isinstance(payload, dict):
        return [Diagnostic("CAP001", f"Capability registry `{path}` must be a JSON object")]
    if payload.get("version") != REGISTRY_VERSION:
        diagnostics.append(
            Diagnostic("CAP001", f"Capability registry `{path}` must declare `version: {REGISTRY_VERSION}`")
        )
    for key in payload:
        if key != "version" and key not in SECTIONS:
            diagnostics.append(Diagnostic("CAP001", f"Unknown capability registry section `{key}` in `{path}`"))
    for section in SECTIONS:
        value = payload.get(section, {})
        if not isinstance(value, dict):
            diagnostics.append(Diagnostic("CAP001", f"Capability registry section `{section}` must be an object"))
            continue
        validator: _EntryValidator = {
            "tools": _validate_tool_entry,
            "hosted_tools": _validate_hosted_tool_entry,
            "agents": _validate_agent_entry,
            "output_types": _validate_output_type_entry,
            "prompts": _validate_prompt_entry,
            "host_context": _validate_host_context_entry,
        }[section]
        for name in sorted(value):
            if not isinstance(name, str) or not name:
                diagnostics.append(Diagnostic("CAP001", f"Capability registry section `{section}` has an invalid key"))
                continue
            diagnostics.extend(validator(section, name, value[name]))
    return diagnostics


def _validate_tool_entry(section: str, name: str, value: Any) -> list[Diagnostic]:
    diagnostics = _entry_object(section, name, value)
    if diagnostics:
        return diagnostics
    assert isinstance(value, dict)
    python = value.get("python")
    external = value.get("external", False)
    if not isinstance(external, bool):
        diagnostics.append(_invalid_entry(section, name, "`external` must be a boolean"))
    if python is not None and not _valid_python_ref(python):
        diagnostics.append(_invalid_entry(section, name, "`python` must use `module:object` syntax"))
    if not python and not external:
        diagnostics.append(_invalid_entry(section, name, "entry must declare `python` or `external: true`"))
    if python and external:
        diagnostics.append(_invalid_entry(section, name, "entry cannot declare both `python` and `external: true`"))
    diagnostics.extend(_validate_permissions(section, name, value))
    return diagnostics


def _validate_hosted_tool_entry(section: str, name: str, value: Any) -> list[Diagnostic]:
    diagnostics = _entry_object(section, name, value)
    if diagnostics:
        return diagnostics
    assert isinstance(value, dict)
    for key in ("provider", "tool"):
        if not isinstance(value.get(key), str) or not value[key]:
            diagnostics.append(_invalid_entry(section, name, f"`{key}` must be a non-empty string"))
    config = value.get("config", {})
    if not isinstance(config, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in config.items()):
        diagnostics.append(_invalid_entry(section, name, "`config` must be an object of string values"))
    agent_configs = value.get("agent_configs", {})
    if not isinstance(agent_configs, dict):
        diagnostics.append(_invalid_entry(section, name, "`agent_configs` must be an object"))
    else:
        for agent_name, agent_config in agent_configs.items():
            if not isinstance(agent_name, str) or not agent_name:
                diagnostics.append(_invalid_entry(section, name, "`agent_configs` keys must be non-empty agent names"))
                continue
            if not isinstance(agent_config, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in agent_config.items()
            ):
                diagnostics.append(
                    _invalid_entry(
                        section,
                        name,
                        f"`agent_configs.{agent_name}` must be an object of string values",
                    )
                )
    factory = value.get("factory")
    if factory is not None and not _valid_python_ref(factory):
        diagnostics.append(_invalid_entry(section, name, "`factory` must use `module:object` syntax"))
    diagnostics.extend(_validate_permissions(section, name, value))
    return diagnostics


def _validate_agent_entry(section: str, name: str, value: Any) -> list[Diagnostic]:
    diagnostics = _entry_object(section, name, value)
    if diagnostics:
        return diagnostics
    assert isinstance(value, dict)
    agent_name = value.get("name")
    factory = value.get("factory")
    if agent_name is not None and (not isinstance(agent_name, str) or not agent_name):
        diagnostics.append(_invalid_entry(section, name, "`name` must be a non-empty string"))
    if factory is not None and not _valid_python_ref(factory):
        diagnostics.append(_invalid_entry(section, name, "`factory` must use `module:object` syntax"))
    if agent_name is None and factory is None:
        diagnostics.append(_invalid_entry(section, name, "entry must declare `name` or `factory`"))
    return diagnostics


def _validate_output_type_entry(section: str, name: str, value: Any) -> list[Diagnostic]:
    diagnostics = _entry_object(section, name, value)
    if diagnostics:
        return diagnostics
    assert isinstance(value, dict)
    if not _valid_python_ref(value.get("python")):
        diagnostics.append(_invalid_entry(section, name, "`python` must use `module:object` syntax"))
    return diagnostics


def _validate_prompt_entry(section: str, name: str, value: Any) -> list[Diagnostic]:
    diagnostics = _entry_object(section, name, value)
    if diagnostics:
        return diagnostics
    assert isinstance(value, dict)
    if not isinstance(value.get("path"), str) or not value["path"]:
        diagnostics.append(_invalid_entry(section, name, "`path` must be a non-empty string"))
    return diagnostics


def _validate_host_context_entry(section: str, name: str, value: Any) -> list[Diagnostic]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        return [_invalid_entry(section, name, "entry must be a list of type names")]
    return []


def _entry_object(section: str, name: str, value: Any) -> list[Diagnostic]:
    if not isinstance(value, dict):
        return [_invalid_entry(section, name, "entry must be an object")]
    return []


def _invalid_entry(section: str, name: str, message: str) -> Diagnostic:
    return Diagnostic("CAP001", f"Invalid capability registry entry `{section}.{name}`: {message}")


def _validate_permissions(section: str, name: str, value: dict[str, Any]) -> list[Diagnostic]:
    if "permission" in value:
        return [_invalid_entry(section, name, "v2 entries must use `permissions`, not `permission`")]
    permissions = value.get("permissions")
    if not isinstance(permissions, dict) or not permissions:
        return [_invalid_entry(section, name, "`permissions` must be a non-empty object")]
    diagnostics: list[Diagnostic] = []
    for agent_name, permission in permissions.items():
        if not isinstance(agent_name, str) or not agent_name:
            diagnostics.append(_invalid_entry(section, name, "`permissions` keys must be non-empty agent names"))
            continue
        if permission not in PERMISSIONS:
            diagnostics.append(
                _invalid_entry(
                    section,
                    name,
                    f"`permissions.{agent_name}` must be one of {', '.join(sorted(PERMISSIONS))}",
                )
            )
    return diagnostics


def _valid_python_ref(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    module, sep, attr = value.partition(":")
    return bool(sep and module.strip() and attr.strip())


__all__ = ["validate_payload"]
