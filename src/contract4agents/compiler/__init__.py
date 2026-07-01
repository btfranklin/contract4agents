"""Provider-neutral compiler for Contract4Agents projects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, TypedDict

from contract4agents.ast import AgentDef, ContractProject, EvalCase, MonitorDef
from contract4agents.diagnostics import ContractError, Diagnostic, raise_if_errors
from contract4agents.guards import GuardPlanItem, build_guard_plan
from contract4agents.parser import parse_project
from contract4agents.schema import type_to_schema
from contract4agents.semantics import analyze_project

JsonSchema = dict[str, Any]
CapabilityStatus = Literal["supported", "partial", "emulated"]


class ManifestUse(TypedDict):
    name: str
    module: str
    permission: str


class ManifestDatasource(TypedDict):
    name: str
    python: str
    produces: str
    requires: list[str]
    render: str
    cache: str


class ManifestInput(TypedDict):
    name: str
    type: str
    required: bool


class ManifestOutput(TypedDict):
    type: str
    schema_ref: str


class AgentManifest(TypedDict):
    agent: str
    description: str
    goal: str
    inputs: list[ManifestInput]
    output: ManifestOutput
    tools: list[ManifestUse]
    agents: list[ManifestUse]
    datasources: list[ManifestDatasource]
    policy: list[str]
    success: list[str]
    routes: list[str]
    composition: list[str]
    guards: list[str]
    assertions: list[str]


class EvalPack(TypedDict):
    name: str
    agent: str
    givens: dict[str, str]
    expects: list[str]
    semantic_expects: list[str]


class MonitorPack(TypedDict):
    name: str
    agent: str
    severity: str
    when: str
    expect: str


class CapabilityEntry(TypedDict):
    status: CapabilityStatus
    caveats: list[str]


CapabilityMatrix = dict[str, dict[str, CapabilityEntry]]


class CompilerArtifacts(TypedDict):
    schemas: dict[str, JsonSchema]
    manifests: dict[str, AgentManifest]
    instructions: dict[str, str]
    evals: list[EvalPack]
    monitors: list[MonitorPack]
    guard_plan: list[GuardPlanItem]
    adapter_capability_matrix: CapabilityMatrix
    docs: dict[str, str]


def compile_project(root: Path | str, output_dir: Path | str | None = None, check: bool = False) -> CompilerArtifacts:
    project = parse_project(root)
    diagnostics = analyze_project(project).diagnostics
    raise_if_errors(diagnostics)
    artifacts = build_artifacts(project)
    if output_dir is not None:
        write_artifacts(artifacts, Path(output_dir), check=check)
    return artifacts


def build_artifacts(project: ContractProject) -> CompilerArtifacts:
    schemas = {name: type_to_schema(type_def) for name, type_def in project.types.items()}
    manifests = {name: agent_manifest(agent, project) for name, agent in project.agents.items()}
    instructions = {name: agent_instructions(agent) for name, agent in project.agents.items()}
    eval_packs = [eval_pack(eval_case) for eval_case in project.evals]
    monitors = [monitor_pack(monitor) for monitor in project.monitors]
    guard_plan = build_guard_plan(manifests)
    capability_matrix = adapter_capability_matrix()
    docs = generated_docs(project, manifests)
    return {
        "schemas": schemas,
        "manifests": manifests,
        "instructions": instructions,
        "evals": eval_packs,
        "monitors": monitors,
        "guard_plan": guard_plan,
        "adapter_capability_matrix": capability_matrix,
        "docs": docs,
    }


def agent_manifest(agent: AgentDef, project: ContractProject) -> AgentManifest:
    tools: list[ManifestUse] = [
        {"name": use.name, "module": use.source, "permission": use.permission}
        for use in agent.uses
        if use.kind == "tool"
    ]
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
        "description": agent.text_attr("description"),
        "goal": agent.text_attr("goal"),
        "inputs": [
            {
                "name": parameter.name,
                "type": parameter.type_name,
                "required": not parameter.nullable and parameter.default is None,
            }
            for parameter in agent.parameters
        ],
        "output": {"type": agent.return_type, "schema_ref": f"schemas/{agent.return_type}.json"},
        "tools": tools,
        "agents": agents,
        "datasources": datasources,
        "policy": agent.list_attr("policy"),
        "success": agent.list_attr("success"),
        "routes": agent.list_attr("routes"),
        "composition": agent.list_attr("composition"),
        "guards": agent.list_attr("guards"),
        "assertions": agent.list_attr("assertions"),
    }


def agent_instructions(agent: AgentDef) -> str:
    lines = [f"# {agent.name}", "", agent.text_attr("goal")]
    description = agent.text_attr("description")
    if description:
        lines.extend(["", f"Description: {description}"])
    for label, key in [
        ("Policy", "policy"),
        ("Success Criteria", "success"),
        ("Routes", "routes"),
        ("Guards", "guards"),
        ("Assertions", "assertions"),
    ]:
        items = agent.list_attr(key)
        if items:
            lines.extend(["", f"## {label}"])
            lines.extend(f"- {item}" for item in items)
    lines.extend(["", f"Return output conforming to `{agent.return_type}`."])
    return "\n".join(lines).strip() + "\n"


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


def adapter_capability_matrix() -> CapabilityMatrix:
    return {
        "openai": {
            "instructions": _capability("supported"),
            "tools": _capability("partial", "Host code supplies SDK function tools from manifest capabilities."),
            "output_schema": _capability("partial", "Host code supplies the SDK output type."),
            "context": _capability(
                "partial", "Contract4Agents resolves context; host code renders it into the SDK prompt/context."
            ),
            "handoff": _capability("partial", "Host code supplies SDK handoff objects when used."),
            "agent_as_tool": _capability("emulated", "Host code wraps child agents as SDK tools."),
            "trace_capture": _capability(
                "partial", "SDK hooks emit normalized lifecycle events; host tools own custom data."
            ),
            "approval_gates": _capability("partial", "Host code resolves SDK approval interruptions."),
            "guards": _capability(
                "partial",
                "Guard plan classifies output conformance, denied tools, and approval-required tools; "
                "host code enforces approvals.",
            ),
            "semantic_judge": _capability("supported"),
        },
    }


def _capability(status: CapabilityStatus, *caveats: str) -> CapabilityEntry:
    return {"status": status, "caveats": list(caveats)}


def generated_docs(project: ContractProject, manifests: dict[str, AgentManifest]) -> dict[str, str]:
    lines = ["# Generated Contract4Agents Summary", ""]
    lines.append("## Agents")
    for name, manifest in sorted(manifests.items()):
        lines.append(f"- `{name}` -> `{manifest['output']['type']}`")
    lines.extend(["", "## Types"])
    for name in sorted(project.types):
        lines.append(f"- `{name}`")
    return {"summary.md": "\n".join(lines) + "\n"}


def write_artifacts(artifacts: CompilerArtifacts, output_dir: Path, check: bool = False) -> None:
    files: dict[Path, str] = {}
    for name, schema in artifacts["schemas"].items():
        files[output_dir / "schemas" / f"{name}.json"] = _json(schema)
    for name, manifest in artifacts["manifests"].items():
        files[output_dir / "manifests" / f"{name}.json"] = _json(manifest)
    for name, instructions in artifacts["instructions"].items():
        files[output_dir / "instructions" / f"{name}.md"] = instructions
    files[output_dir / "evals" / "evals.json"] = _json(artifacts["evals"])
    files[output_dir / "monitors" / "monitors.json"] = _json(artifacts["monitors"])
    files[output_dir / "guards" / "guard-plan.json"] = _json(artifacts["guard_plan"])
    files[output_dir / "adapters" / "capability-matrix.json"] = _json(artifacts["adapter_capability_matrix"])
    for name, text in artifacts["docs"].items():
        files[output_dir / "docs" / name] = text
    if check:
        stale = [path for path, content in files.items() if not path.exists() or path.read_text() != content]
        if stale:
            raise ContractError(
                [
                    Diagnostic(
                        "COMPILE001",
                        "Generated artifacts are stale",
                        hint="Rerun compile without --check to refresh generated artifacts.\n"
                        + "Stale files:\n"
                        + "\n".join(str(path) for path in stale[:10]),
                    )
                ]
            )
        return
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


__all__ = [
    "AgentManifest",
    "CapabilityEntry",
    "CapabilityMatrix",
    "CompilerArtifacts",
    "EvalPack",
    "GuardPlanItem",
    "JsonSchema",
    "ManifestDatasource",
    "ManifestInput",
    "ManifestOutput",
    "ManifestUse",
    "MonitorPack",
    "adapter_capability_matrix",
    "agent_instructions",
    "agent_manifest",
    "build_artifacts",
    "compile_project",
    "eval_pack",
    "generated_docs",
    "monitor_pack",
    "write_artifacts",
]
