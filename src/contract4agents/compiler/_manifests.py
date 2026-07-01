"""Provider-neutral manifest, eval, and monitor artifact generation."""

from __future__ import annotations

from typing import Any

from contract4agents.ast import AgentDef, ContractProject, EvalCase, MonitorDef, UseDecl
from contract4agents.compiler._types import (
    AgentManifest,
    EvalPack,
    ManifestDatasource,
    ManifestHostedTool,
    ManifestUse,
    MonitorPack,
)
from contract4agents.hosted_tools import split_hosted_tool_name


def agent_manifest(agent: AgentDef, project: ContractProject) -> AgentManifest:
    types = project.types
    tools: list[ManifestUse] = [
        {"name": use.name, "module": use.source, "permission": use.permission}
        for use in agent.uses
        if use.kind == "tool"
    ]
    hosted_tools = [_hosted_tool_manifest(use) for use in agent.uses if use.kind == "hosted_tool"]
    agents: list[ManifestUse] = [
        {"name": use.name, "module": use.source, "permission": use.permission}
        for use in agent.uses
        if use.kind == "agent"
    ]
    datasources: list[ManifestDatasource] = []
    for use in agent.uses:
        if use.kind != "datasource":
            continue
        datasource = project.datasources.get(use.name)
        if datasource:
            datasources.append(
                {
                    "name": datasource.name,
                    "python": datasource.python,
                    "produces": datasource.produces,
                    "requires": datasource.requires,
                    "render": datasource.render,
                    "cache": datasource.cache,
                }
            )
    return {
        "agent": agent.name,
        "source_path": _source_path(agent, project),
        "description": agent.text_attr("description"),
        "goal": agent.text_attr("goal"),
        "inputs": [
            {
                "name": parameter.name,
                "type": parameter.type_name,
                "required": not parameter.nullable and parameter.default is None,
                "python_ref": _python_ref_for_type(parameter.type_name, types),
            }
            for parameter in agent.parameters
        ],
        "output": {
            "type": agent.return_type,
            "schema_ref": f"schemas/{agent.return_type}.json",
            "python_ref": _python_ref_for_type(agent.return_type, types),
        },
        "tools": tools,
        "hosted_tools": hosted_tools,
        "agents": agents,
        "datasources": datasources,
        "policy": agent.list_attr("policy"),
        "success": agent.list_attr("success"),
        "routes": agent.list_attr("routes"),
        "composition": agent.list_attr("composition"),
        "guards": agent.list_attr("guards"),
        "assertions": agent.list_attr("assertions"),
    }


def eval_pack(eval_case: EvalCase) -> EvalPack:
    return {
        "name": eval_case.name,
        "agent": eval_case.agent,
        "givens": eval_case.givens,
        "expects": eval_case.expects,
        "semantic_expects": eval_case.semantic_expects,
    }


def monitor_pack(monitor: MonitorDef) -> MonitorPack:
    return {
        "name": monitor.name,
        "agent": monitor.agent,
        "severity": monitor.severity,
        "when": monitor.condition,
        "expect": monitor.expectation,
    }


def _python_ref_for_type(type_name: str, types: dict[str, Any]) -> str | None:
    normalized = type_name.rstrip("?")
    if normalized.endswith("[]"):
        normalized = normalized[:-2]
    if normalized.startswith("list[") and normalized.endswith("]"):
        normalized = normalized[5:-1]
    type_def = types.get(normalized)
    return type_def.python_ref if type_def and type_def.source == "python" else None


def _source_path(agent: AgentDef, project: ContractProject) -> str:
    try:
        return str(agent.span.path.relative_to(project.root))
    except ValueError:
        return str(agent.span.path)


def _hosted_tool_manifest(use: UseDecl) -> ManifestHostedTool:
    split_name = split_hosted_tool_name(use.name)
    provider, tool = split_name if split_name is not None else ("", "")
    return {
        "name": use.name,
        "provider": provider,
        "tool": tool,
        "config": dict(use.config),
        "permission": use.permission,
    }


__all__ = ["agent_manifest", "eval_pack", "monitor_pack"]
