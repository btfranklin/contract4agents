"""Capability registry loading and host-code drift checks."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from contract4agents.ast import ContractProject
from contract4agents.compiler import CompilerArtifacts
from contract4agents.diagnostics import Diagnostic
from contract4agents.runtime import load_python_ref

DEFAULT_REGISTRY_FILENAME = "contract4agents.registry.json"
_SECTIONS = ("tools", "hosted_tools", "agents", "output_types", "prompts", "host_context")
_PERMISSIONS = {"available", "preapproved", "requires_approval", "denied", "sandboxed"}


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


def load_capability_registry(
    root: Path | str,
    registry_path: Path | str | None = None,
    *,
    required: bool = False,
) -> CapabilityRegistryLoad:
    """Load and validate the optional project capability registry."""

    root_path = Path(root)
    path = _registry_path(root_path, registry_path)
    explicit = registry_path is not None
    if not path.exists():
        if required or explicit:
            return CapabilityRegistryLoad(
                path=path,
                registry=None,
                diagnostics=[
                    Diagnostic(
                        "CAP002",
                        f"Capability registry `{path}` was not found",
                        hint=f"Add `{DEFAULT_REGISTRY_FILENAME}` or pass --registry PATH.",
                    )
                ],
            )
        return CapabilityRegistryLoad(path=path, registry=None, diagnostics=[])
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return CapabilityRegistryLoad(
            path=path,
            registry=None,
            diagnostics=[
                Diagnostic("CAP001", f"Invalid capability registry JSON in `{path}`", hint=str(exc))
            ],
        )
    diagnostics = _validate_payload(payload, path)
    if diagnostics:
        return CapabilityRegistryLoad(path=path, registry=None, diagnostics=diagnostics)
    assert isinstance(payload, dict)
    return CapabilityRegistryLoad(
        path=path,
        registry=CapabilityRegistry(
            path=path,
            tools=payload.get("tools", {}),
            hosted_tools=payload.get("hosted_tools", {}),
            agents=payload.get("agents", {}),
            output_types=payload.get("output_types", {}),
            prompts=payload.get("prompts", {}),
            host_context=payload.get("host_context", {}),
        ),
        diagnostics=[],
    )


def check_capability_drift(
    project: ContractProject,
    artifacts: CompilerArtifacts,
    registry: CapabilityRegistry | None,
) -> list[Diagnostic]:
    """Compare compiled contract artifacts with a loaded capability registry."""

    if registry is None:
        return []
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_check_tools(project, artifacts, registry))
    diagnostics.extend(_check_hosted_tools(project, artifacts, registry))
    diagnostics.extend(_check_agents(project, artifacts, registry))
    diagnostics.extend(_check_output_types(project, artifacts, registry))
    diagnostics.extend(_check_prompts(project, artifacts, registry))
    diagnostics.extend(_check_host_context(project, artifacts, registry))
    return diagnostics


def _registry_path(root: Path, registry_path: Path | str | None) -> Path:
    if registry_path is None:
        return root / DEFAULT_REGISTRY_FILENAME
    path = Path(registry_path)
    return path if path.is_absolute() else root / path


def _validate_payload(payload: Any, path: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if not isinstance(payload, dict):
        return [Diagnostic("CAP001", f"Capability registry `{path}` must be a JSON object")]
    if payload.get("version") != 1:
        diagnostics.append(Diagnostic("CAP001", f"Capability registry `{path}` must declare `version: 1`"))
    for key in payload:
        if key != "version" and key not in _SECTIONS:
            diagnostics.append(Diagnostic("CAP001", f"Unknown capability registry section `{key}` in `{path}`"))
    for section in _SECTIONS:
        value = payload.get(section, {})
        if not isinstance(value, dict):
            diagnostics.append(Diagnostic("CAP001", f"Capability registry section `{section}` must be an object"))
            continue
        validator = {
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
    diagnostics.extend(_validate_permission(section, name, value.get("permission")))
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
    factory = value.get("factory")
    if factory is not None and not _valid_python_ref(factory):
        diagnostics.append(_invalid_entry(section, name, "`factory` must use `module:object` syntax"))
    diagnostics.extend(_validate_permission(section, name, value.get("permission")))
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


def _validate_permission(section: str, name: str, value: Any) -> list[Diagnostic]:
    if value not in _PERMISSIONS:
        return [_invalid_entry(section, name, f"`permission` must be one of {', '.join(sorted(_PERMISSIONS))}")]
    return []


def _valid_python_ref(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    module, sep, attr = value.partition(":")
    return bool(sep and module.strip() and attr.strip())


def _check_tools(
    project: ContractProject,
    artifacts: CompilerArtifacts,
    registry: CapabilityRegistry,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for tool in _manifest_tools(artifacts):
        name = tool["name"]
        entry = registry.tools.get(name)
        if entry is None:
            diagnostics.append(
                Diagnostic(
                    "CAP010",
                    f"Tool `{name}` is declared by `{tool['agent']}` but missing from `{registry.path}`",
                    hint=f"Add `tools.{name}` with a Python callable or `external: true`.",
                )
            )
            continue
        diagnostics.extend(_check_permission("tool", name, tool["permission"], entry["permission"]))
        if entry.get("external") is True:
            continue
        loaded = _load_registry_ref(project.root, f"tools.{name}", entry["python"])
        diagnostics.extend(loaded.diagnostics)
        if loaded.value is not None and not callable(loaded.value):
            diagnostics.append(
                Diagnostic(
                    "CAP021",
                    f"Python ref `{entry['python']}` for tool `{name}` is not callable",
                    hint="Tool registry entries must point at a callable surface.",
                )
            )
    return diagnostics


def _check_hosted_tools(
    project: ContractProject,
    artifacts: CompilerArtifacts,
    registry: CapabilityRegistry,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for hosted_tool in _manifest_hosted_tools(artifacts):
        name = hosted_tool["name"]
        entry = registry.hosted_tools.get(name)
        if entry is None:
            diagnostics.append(
                Diagnostic(
                    "CAP010",
                    f"Hosted tool `{name}` is declared by `{hosted_tool['agent']}` but missing from `{registry.path}`",
                    hint=f"Add `hosted_tools.{name}` with provider, tool, config, and permission.",
                )
            )
            continue
        expected = {
            "provider": hosted_tool["provider"],
            "tool": hosted_tool["tool"],
            "config": hosted_tool["config"],
            "permission": hosted_tool["permission"],
        }
        actual = {
            "provider": entry["provider"],
            "tool": entry["tool"],
            "config": entry.get("config", {}),
            "permission": entry["permission"],
        }
        if actual != expected:
            diagnostics.append(
                Diagnostic(
                    "CAP060",
                    f"Hosted tool `{name}` in `{registry.path}` does not match the contract declaration",
                    hint=f"Expected {expected}; registry has {actual}.",
                )
            )
        factory = entry.get("factory")
        if factory is not None:
            loaded = _load_registry_ref(project.root, f"hosted_tools.{name}.factory", factory)
            diagnostics.extend(loaded.diagnostics)
            if loaded.value is not None and not callable(loaded.value):
                diagnostics.append(
                    Diagnostic("CAP021", f"Python ref `{factory}` for hosted tool `{name}` factory is not callable")
                )
    return diagnostics


def _check_agents(
    project: ContractProject,
    artifacts: CompilerArtifacts,
    registry: CapabilityRegistry,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for name in sorted(artifacts["manifests"]):
        entry = registry.agents.get(name)
        if entry is None:
            diagnostics.append(
                Diagnostic(
                    "CAP010",
                    f"Agent `{name}` is missing from `{registry.path}`",
                    hint=f"Add `agents.{name}` with an SDK agent name or factory import path.",
                )
            )
            continue
        actual_name = entry.get("name")
        if actual_name is not None and actual_name != name:
            diagnostics.append(
                Diagnostic(
                    "CAP040",
                    f"Agent `{name}` is registered as host agent `{actual_name}`",
                    hint=f"Expected registry `agents.{name}.name` to be `{name}`.",
                )
            )
        factory = entry.get("factory")
        if factory is not None:
            loaded = _load_registry_ref(project.root, f"agents.{name}.factory", factory)
            diagnostics.extend(loaded.diagnostics)
            if loaded.value is not None and not callable(loaded.value):
                diagnostics.append(
                    Diagnostic("CAP021", f"Python ref `{factory}` for agent `{name}` factory is not callable")
                )
    return diagnostics


def _check_output_types(
    project: ContractProject,
    artifacts: CompilerArtifacts,
    registry: CapabilityRegistry,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for name in sorted(registry.output_types):
        entry = registry.output_types[name]
        if name not in artifacts["schemas"]:
            diagnostics.append(
                Diagnostic("CAP050", f"Output type registry entry `{name}` does not match any contract type")
            )
            continue
        loaded = _load_registry_ref(project.root, f"output_types.{name}", entry["python"])
        diagnostics.extend(loaded.diagnostics)
        if loaded.value is None:
            continue
        if not isinstance(loaded.value, type) or not issubclass(loaded.value, BaseModel):
            diagnostics.append(
                Diagnostic(
                    "CAP050",
                    f"Output type `{name}` registry ref `{entry['python']}` is not a Pydantic BaseModel class",
                )
            )
            continue
        try:
            host_schema = loaded.value.model_json_schema(ref_template="#/$defs/{model}")
        except Exception as exc:
            diagnostics.append(
                Diagnostic(
                    "CAP050",
                    f"Could not derive JSON Schema for output type `{name}` from `{entry['python']}`",
                    hint=str(exc),
                )
            )
            continue
        contract_signature = _schema_signature(artifacts["schemas"][name])
        host_signature = _schema_signature(host_schema)
        if contract_signature != host_signature:
            diagnostics.append(
                Diagnostic(
                    "CAP050",
                    f"Output type `{name}` in `{registry.path}` does not match the contract schema",
                    hint=f"Expected {contract_signature}; registry ref produced {host_signature}.",
                )
            )
    return diagnostics


def _check_prompts(
    project: ContractProject,
    artifacts: CompilerArtifacts,
    registry: CapabilityRegistry,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    agent_names = set(artifacts["manifests"])
    for name in sorted(registry.prompts):
        entry = registry.prompts[name]
        if name not in agent_names:
            diagnostics.append(
                Diagnostic("CAP070", f"Prompt registry entry `{name}` does not match any contract agent")
            )
            continue
        prompt_path = project.root / entry["path"]
        if not prompt_path.exists():
            diagnostics.append(
                Diagnostic(
                    "CAP070",
                    f"Prompt asset `{entry['path']}` for agent `{name}` was not found",
                    hint=f"Expected `{prompt_path}` to exist.",
                )
            )
    return diagnostics


def _check_host_context(
    project: ContractProject,
    artifacts: CompilerArtifacts,
    registry: CapabilityRegistry,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    known_types = set(artifacts["schemas"])
    for agent_name, type_names in sorted(registry.host_context.items()):
        if agent_name not in artifacts["manifests"]:
            diagnostics.append(
                Diagnostic("CAP080", f"Host context registry entry `{agent_name}` does not match any contract agent")
            )
        for type_name in type_names:
            if type_name not in known_types:
                diagnostics.append(
                    Diagnostic("CAP080", f"Host context registry entry `{agent_name}.{type_name}` uses unknown type")
                )
    for agent_name, manifest in sorted(artifacts["manifests"].items()):
        registered = set(registry.host_context.get(agent_name, []))
        for entry in manifest["host_context"]:
            type_name = entry["type"]
            if type_name not in registered:
                diagnostics.append(
                    Diagnostic(
                        "CAP080",
                        f"Agent `{agent_name}` declares host_context `{type_name}` "
                        f"but `{registry.path}` does not mark it host-provided",
                        hint=f"Add `{type_name}` to `host_context.{agent_name}`.",
                    )
                )
    return diagnostics


def _check_permission(kind: str, name: str, expected: str, actual: Any) -> list[Diagnostic]:
    if expected == actual:
        return []
    return [
        Diagnostic(
            "CAP030",
            f"{kind.title()} `{name}` permission mismatch",
            hint=f"Contract declares `{expected}` but registry declares `{actual}`.",
        )
    ]


def _manifest_tools(artifacts: CompilerArtifacts) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for agent_name, manifest in sorted(artifacts["manifests"].items()):
        for tool in manifest["tools"]:
            items.append(
                {
                    "agent": agent_name,
                    "name": tool["name"],
                    "module": tool["module"],
                    "permission": tool["permission"],
                }
            )
    return sorted(items, key=lambda item: (item["name"], item["agent"]))


def _manifest_hosted_tools(artifacts: CompilerArtifacts) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for agent_name, manifest in sorted(artifacts["manifests"].items()):
        for hosted_tool in manifest["hosted_tools"]:
            items.append({"agent": agent_name, **hosted_tool})
    return sorted(items, key=lambda item: (item["name"], item["agent"]))


@dataclass(frozen=True)
class _LoadedRef:
    value: Any | None
    diagnostics: list[Diagnostic]


def _load_registry_ref(root: Path, registry_entry: str, reference: str) -> _LoadedRef:
    try:
        with _project_import_path(root):
            return _LoadedRef(load_python_ref(reference), [])
    except Exception as exc:
        return _LoadedRef(
            None,
            [
                Diagnostic(
                    "CAP020",
                    f"Could not import `{reference}` for capability registry entry `{registry_entry}`",
                    hint=str(exc),
                )
            ],
        )


@contextmanager
def _project_import_path(root: Path) -> Iterator[None]:
    path = str(root.resolve())
    inserted = path not in sys.path
    if inserted:
        sys.path.insert(0, path)
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(path)
            except ValueError:
                pass


def _schema_signature(schema: Mapping[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []
    return {
        "required": sorted(item for item in required if isinstance(item, str)),
        "properties": {
            str(name): _normalize_schema_fragment(value)
            for name, value in sorted(properties.items())
            if isinstance(value, Mapping)
        },
    }


def _normalize_schema_fragment(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_schema_fragment(child)
            for key, child in sorted(value.items())
            if key not in {"title", "description", "default", "examples"}
        }
    if isinstance(value, list):
        return [_normalize_schema_fragment(item) for item in value]
    return value


__all__ = [
    "CapabilityRegistry",
    "CapabilityRegistryLoad",
    "DEFAULT_REGISTRY_FILENAME",
    "check_capability_drift",
    "load_capability_registry",
]
