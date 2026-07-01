"""Provider-neutral compiler for Contract4Agents projects."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, TypedDict

from contract4agents.ast import AgentDef, ContractProject, EvalCase, MonitorDef, UseDecl
from contract4agents.diagnostics import ContractError, Diagnostic, raise_if_errors
from contract4agents.guards import GuardPlanItem, build_guard_plan
from contract4agents.hosted_tools import split_hosted_tool_name
from contract4agents.parser import parse_project
from contract4agents.pydantic_interop import PydanticSchemaError, schema_from_pydantic_type
from contract4agents.schema import type_to_schema
from contract4agents.semantics import analyze_project

JsonSchema = dict[str, Any]
CapabilityStatus = Literal["supported", "partial", "emulated"]


class ManifestUse(TypedDict):
    name: str
    module: str
    permission: str


class ManifestHostedTool(TypedDict):
    name: str
    provider: str
    tool: str
    config: dict[str, str]
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
    python_ref: str | None


class ManifestOutput(TypedDict):
    type: str
    schema_ref: str
    python_ref: str | None


class AgentManifest(TypedDict):
    agent: str
    source_path: str
    description: str
    goal: str
    inputs: list[ManifestInput]
    output: ManifestOutput
    tools: list[ManifestUse]
    hosted_tools: list[ManifestHostedTool]
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


class TypeBinding(TypedDict):
    type: str
    source: Literal["native", "python"]
    python_ref: str | None
    schema_ref: str
    schema_hash: str


class CompilerArtifacts(TypedDict):
    schemas: dict[str, JsonSchema]
    type_bindings: list[TypeBinding]
    manifests: dict[str, AgentManifest]
    instructions: dict[str, str]
    evals: list[EvalPack]
    monitors: list[MonitorPack]
    guard_plan: list[GuardPlanItem]
    adapter_capability_matrix: CapabilityMatrix
    docs: dict[str, str]


def compile_project(
    root: Path | str,
    output_dir: Path | str | None = None,
    check: bool = False,
    allow_python_imports: bool = False,
) -> CompilerArtifacts:
    project = parse_project(root)
    diagnostics = analyze_project(project).diagnostics
    raise_if_errors(diagnostics)
    artifacts = build_artifacts(project, allow_python_imports=allow_python_imports)
    if output_dir is not None:
        write_artifacts(artifacts, Path(output_dir), check=check)
    return artifacts


def build_artifacts(project: ContractProject, allow_python_imports: bool = False) -> CompilerArtifacts:
    schemas, type_bindings = build_type_artifacts(project, allow_python_imports=allow_python_imports)
    manifests = {name: agent_manifest(agent, project) for name, agent in project.agents.items()}
    instructions = {name: agent_instructions(agent) for name, agent in project.agents.items()}
    eval_packs = [eval_pack(eval_case) for eval_case in project.evals]
    monitors = [monitor_pack(monitor) for monitor in project.monitors]
    guard_plan = build_guard_plan(manifests)
    capability_matrix = adapter_capability_matrix()
    docs = generated_docs(project, manifests)
    return {
        "schemas": schemas,
        "type_bindings": type_bindings,
        "manifests": manifests,
        "instructions": instructions,
        "evals": eval_packs,
        "monitors": monitors,
        "guard_plan": guard_plan,
        "adapter_capability_matrix": capability_matrix,
        "docs": docs,
    }


def build_type_artifacts(
    project: ContractProject,
    allow_python_imports: bool = False,
) -> tuple[dict[str, JsonSchema], list[TypeBinding]]:
    schemas: dict[str, JsonSchema] = {}
    bindings: list[TypeBinding] = []
    for name, type_def in project.types.items():
        if type_def.source == "python":
            if not allow_python_imports:
                raise ContractError(
                    [
                        Diagnostic(
                            "PYD000",
                            f"Python imports are disabled for imported type `{name}`",
                            span=type_def.span,
                            hint="Run check or compile with --allow-python-imports to derive schemas from host models.",
                        )
                    ]
                )
            try:
                schema, schema_hash = schema_from_pydantic_type(type_def)
            except PydanticSchemaError as exc:
                raise ContractError([exc.diagnostic]) from exc
        else:
            schema = type_to_schema(type_def)
            schema_hash = _schema_hash(schema)
        schemas[name] = schema
        bindings.append(
            {
                "type": name,
                "source": type_def.source,
                "python_ref": type_def.python_ref,
                "schema_ref": f"schemas/{name}.json",
                "schema_hash": schema_hash,
            }
        )
    return schemas, bindings


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
            "hosted_tools": _capability(
                "partial", "Host code enables provider-native hosted tools through explicit adapter registries."
            ),
            "output_schema": _capability(
                "partial", "Host code supplies SDK output types or uses adapter-generated Pydantic models."
            ),
            "context": _capability(
                "partial",
                "Contract4Agents resolves context; the OpenAI run helper renders non-sensitive runtime context.",
            ),
            "handoff": _capability("partial", "Host code supplies SDK handoff objects when used."),
            "agent_as_tool": _capability("emulated", "Host code wraps child agents as SDK tools."),
            "trace_capture": _capability(
                "partial", "SDK hooks emit normalized lifecycle events; host tools own custom data."
            ),
            "approval_gates": _capability(
                "partial", "The OpenAI run helper resolves SDK approval interruptions through host callbacks."
            ),
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
    hosted_tools = [
        (agent_name, tool)
        for agent_name, manifest in sorted(manifests.items())
        for tool in manifest["hosted_tools"]
    ]
    if hosted_tools:
        lines.extend(["", "## Hosted Tools"])
        for agent_name, tool in hosted_tools:
            config = ", ".join(f"{key}={value}" for key, value in sorted(tool["config"].items()))
            suffix = f" ({config})" if config else ""
            lines.append(f"- `{agent_name}` may use `{tool['name']}`{suffix}")
    return {"summary.md": "\n".join(lines) + "\n"}


def write_artifacts(artifacts: CompilerArtifacts, output_dir: Path, check: bool = False) -> None:
    files: dict[Path, str] = {}
    for name, schema in artifacts["schemas"].items():
        files[output_dir / "schemas" / f"{name}.json"] = _json(schema)
    files[output_dir / "types" / "type-bindings.json"] = _json(artifacts["type_bindings"])
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


def _schema_hash(schema: JsonSchema) -> str:
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = [
    "AgentManifest",
    "CapabilityEntry",
    "CapabilityMatrix",
    "CompilerArtifacts",
    "EvalPack",
    "GuardPlanItem",
    "JsonSchema",
    "ManifestHostedTool",
    "ManifestDatasource",
    "ManifestInput",
    "ManifestOutput",
    "ManifestUse",
    "MonitorPack",
    "TypeBinding",
    "adapter_capability_matrix",
    "agent_instructions",
    "agent_manifest",
    "build_artifacts",
    "build_type_artifacts",
    "compile_project",
    "eval_pack",
    "generated_docs",
    "monitor_pack",
    "write_artifacts",
]
