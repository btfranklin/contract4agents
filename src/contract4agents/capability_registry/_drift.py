"""Capability registry drift checks against compiled artifacts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from contract4agents.ast import ContractProject
from contract4agents.capability_registry._imports import load_registry_ref
from contract4agents.capability_registry._models import CapabilityRegistry
from contract4agents.capability_registry._schema import schema_signature
from contract4agents.compiler import CompilerArtifacts
from contract4agents.diagnostics import Diagnostic


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


def _check_tools(
    project: ContractProject,
    artifacts: CompilerArtifacts,
    registry: CapabilityRegistry,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    expected_tools = _manifest_tool_permissions(artifacts)
    for stale_name in sorted(set(registry.tools) - set(expected_tools)):
        diagnostics.append(
            Diagnostic(
                "CAP090",
                f"Tool registry entry `{stale_name}` does not match any contract declaration",
            )
        )
    for name, expected_permissions in expected_tools.items():
        entry = registry.tools.get(name)
        if entry is None:
            diagnostics.append(
                Diagnostic(
                    "CAP010",
                    f"Tool `{name}` is declared but missing from `{registry.path}`",
                    hint=f"Add `tools.{name}` with a Python callable or `external: true`.",
                )
            )
            continue
        diagnostics.extend(_check_permissions("tool", name, expected_permissions, entry.get("permissions", {})))
        if entry.get("external") is True:
            continue
        loaded = load_registry_ref(project.root, f"tools.{name}", entry["python"])
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
    expected_hosted_tools = _manifest_hosted_tool_permissions(artifacts)
    for stale_name in sorted(set(registry.hosted_tools) - set(expected_hosted_tools)):
        diagnostics.append(
            Diagnostic(
                "CAP090",
                f"Hosted tool registry entry `{stale_name}` does not match any contract declaration",
            )
        )
    for name, expected_item in expected_hosted_tools.items():
        entry = registry.hosted_tools.get(name)
        if entry is None:
            diagnostics.append(
                Diagnostic(
                    "CAP010",
                    f"Hosted tool `{name}` is declared but missing from `{registry.path}`",
                    hint=f"Add `hosted_tools.{name}` with provider, tool, config, and permissions.",
                )
            )
            continue
        expected = {
            "provider": expected_item["provider"],
            "tool": expected_item["tool"],
            "config": expected_item["config"],
        }
        actual = {
            "provider": entry["provider"],
            "tool": entry["tool"],
            "config": entry.get("config", {}),
        }
        if actual != expected:
            diagnostics.append(
                Diagnostic(
                    "CAP060",
                    f"Hosted tool `{name}` in `{registry.path}` does not match the contract declaration",
                    hint=f"Expected {expected}; registry has {actual}.",
                )
            )
        diagnostics.extend(
            _check_permissions(
                "hosted tool",
                name,
                expected_item["permissions"],
                entry.get("permissions", {}),
            )
        )
        factory = entry.get("factory")
        if factory is not None:
            loaded = load_registry_ref(project.root, f"hosted_tools.{name}.factory", factory)
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
    for stale_name in sorted(set(registry.agents) - set(artifacts["manifests"])):
        diagnostics.append(
            Diagnostic(
                "CAP090",
                f"Agent registry entry `{stale_name}` does not match any contract agent",
            )
        )
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
            loaded = load_registry_ref(project.root, f"agents.{name}.factory", factory)
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
    python_type_names = {item["type"] for item in artifacts["type_bindings"] if item["source"] == "python"}
    agent_output_types = {
        manifest["output"]["type"]
        for manifest in artifacts["manifests"].values()
        if manifest["output"]["type"] in python_type_names
    }
    for name in sorted(agent_output_types - set(registry.output_types)):
        diagnostics.append(
            Diagnostic(
                "CAP050",
                f"Python-backed output type `{name}` is missing from `{registry.path}`",
                hint=f"Add `output_types.{name}` with the host Pydantic model import path.",
            )
        )
    for name in sorted(registry.output_types):
        entry = registry.output_types[name]
        if name not in artifacts["schemas"]:
            diagnostics.append(
                Diagnostic("CAP050", f"Output type registry entry `{name}` does not match any contract type")
            )
            continue
        loaded = load_registry_ref(project.root, f"output_types.{name}", entry["python"])
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
        contract_signature = schema_signature(artifacts["schemas"][name])
        host_signature = schema_signature(host_schema)
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


def _check_permissions(
    kind: str,
    name: str,
    expected: dict[str, str],
    actual: Any,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    actual_permissions = actual if isinstance(actual, dict) else {}
    for agent_name, expected_permission in sorted(expected.items()):
        actual_permission = actual_permissions.get(agent_name)
        if actual_permission == expected_permission:
            continue
        diagnostics.append(
            Diagnostic(
                "CAP030",
                f"{kind.title()} `{name}` permission mismatch for agent `{agent_name}`",
                hint=f"Contract declares `{expected_permission}` but registry declares `{actual_permission}`.",
            )
        )
    for stale_agent in sorted(set(actual_permissions) - set(expected)):
        diagnostics.append(
            Diagnostic(
                "CAP090",
                f"{kind.title()} `{name}` registry permission for `{stale_agent}` "
                "does not match any contract declaration",
            )
        )
    return diagnostics


def _manifest_tool_permissions(artifacts: CompilerArtifacts) -> dict[str, dict[str, str]]:
    permissions: dict[str, dict[str, str]] = {}
    for tool in _manifest_tools(artifacts):
        permissions.setdefault(tool["name"], {})[tool["agent"]] = tool["permission"]
    return permissions


def _manifest_hosted_tool_permissions(artifacts: CompilerArtifacts) -> dict[str, dict[str, Any]]:
    hosted_tools: dict[str, dict[str, Any]] = {}
    for hosted_tool in _manifest_hosted_tools(artifacts):
        name = hosted_tool["name"]
        item = hosted_tools.setdefault(
            name,
            {
                "provider": hosted_tool["provider"],
                "tool": hosted_tool["tool"],
                "config": hosted_tool["config"],
                "permissions": {},
            },
        )
        item["permissions"][hosted_tool["agent"]] = hosted_tool["permission"]
    return hosted_tools


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


__all__ = ["check_capability_drift"]
