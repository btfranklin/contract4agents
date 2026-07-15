"""Canonical V2 compilation from source through portable generated artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from contract4agents.codegen import GeneratedCode, generate_code
from contract4agents.diagnostics import raise_if_errors
from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    FrozenMap,
    ListTypeRef,
    MapTypeRef,
    NamedTypeRef,
    NullableTypeRef,
    PrimitiveTypeRef,
    TypeIR,
    TypeRef,
    build_canonical_ir,
    contract_digest,
    format_type_ref,
    semantic_id,
)
from contract4agents.output_paths import validate_output_dir
from contract4agents.parser import parse_project
from contract4agents.semantics import analyze_project


@dataclass(frozen=True)
class CompilerArtifacts:
    """The deterministic portable artifacts derived from one canonical IR."""

    ir: CanonicalIR
    contract_digest: str
    schemas: FrozenMap[str, dict[str, object]]
    instructions: FrozenMap[str, str]
    docs: FrozenMap[PurePosixPath, str]
    generated_code: GeneratedCode


def compile_project(
    root: Path | str,
    output_dir: Path | str | None = None,
    *,
    check: bool = False,
) -> CompilerArtifacts:
    """Compile a V2 project, optionally writing or checking its artifacts."""

    project = parse_project(root)
    raise_if_errors(analyze_project(project).diagnostics)
    artifacts = build_artifacts(build_canonical_ir(project))
    if output_dir is not None:
        output_path = validate_output_dir(project.root, output_dir, artifact_label="compiler artifacts")
        from contract4agents.compiler._v2_writer import write_artifacts

        write_artifacts(artifacts, output_path, check=check)
    return artifacts


def build_artifacts(ir: CanonicalIR) -> CompilerArtifacts:
    """Build every target-independent V2 artifact from canonical IR only."""

    digest = contract_digest(ir)
    schemas = FrozenMap((item.name, _schema_for_type(item, ir)) for item in ir.types.values())
    instructions = FrozenMap((agent.name, _instructions(agent, ir)) for agent in ir.agents.values())
    docs = _generated_docs(ir, digest)
    return CompilerArtifacts(
        ir=ir,
        contract_digest=digest,
        schemas=schemas,
        instructions=instructions,
        docs=docs,
        generated_code=generate_code(ir),
    )


def artifact_digests(artifacts: CompilerArtifacts) -> FrozenMap[str, str]:
    """Return stable content digests used by planning and assurance."""

    values: list[tuple[str, str]] = []
    for name, schema in artifacts.schemas.items():
        values.append((f"schemas/{name}.json", _digest_json(schema)))
    for name, source in artifacts.instructions.items():
        values.append((f"instructions/{name}.md", _digest_text(source)))
    for path, source in artifacts.generated_code.files.items():
        values.append((f"generated/{path}", _digest_text(source)))
    return FrozenMap(values)


def _schema_for_type(type_def: TypeIR, ir: CanonicalIR) -> dict[str, object]:
    required: list[str] = []
    properties: dict[str, object] = {}
    referenced: set[str] = set()
    for item in type_def.fields:
        field_schema = _schema_for_ref(item.type_ref, referenced)
        if item.has_default:
            field_schema["default"] = _plain_json(item.default)
        elif not isinstance(item.type_ref, NullableTypeRef):
            required.append(item.name)
        properties[item.name] = field_schema
    schema: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:contract4agents:type:{type_def.name}",
        "title": type_def.name,
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    if referenced:
        definitions: dict[str, object] = {}
        pending = sorted(referenced)
        while pending:
            name = pending.pop(0)
            if name in definitions:
                continue
            target = ir.types.get(semantic_id("type", name))
            if target is None:
                raise ValueError(f"Type `{type_def.name}` references unknown type `{name}`")
            nested_refs: set[str] = set()
            nested_properties: dict[str, object] = {}
            nested_required: list[str] = []
            for field in target.fields:
                field_schema = _schema_for_ref(field.type_ref, nested_refs)
                if field.has_default:
                    field_schema["default"] = _plain_json(field.default)
                elif not isinstance(field.type_ref, NullableTypeRef):
                    nested_required.append(field.name)
                nested_properties[field.name] = field_schema
            definition: dict[str, object] = {
                "title": target.name,
                "type": "object",
                "properties": nested_properties,
                "additionalProperties": False,
            }
            if nested_required:
                definition["required"] = nested_required
            definitions[name] = definition
            pending.extend(sorted(nested_refs - definitions.keys()))
        schema["$defs"] = {name: definitions[name] for name in sorted(definitions)}
    return schema


def _schema_for_ref(type_ref: TypeRef, referenced: set[str]) -> dict[str, object]:
    if isinstance(type_ref, PrimitiveTypeRef):
        primitive_schemas: dict[str, dict[str, object]] = {
            "string": {"type": "string"},
            "integer": {"type": "integer"},
            "float": {"type": "number"},
            "boolean": {"type": "boolean"},
            "datetime": {"type": "string", "format": "date-time"},
        }
        return primitive_schemas[type_ref.name]
    if isinstance(type_ref, NamedTypeRef):
        name = type_ref.type_id.parts[0]
        referenced.add(name)
        return {"$ref": f"#/$defs/{name}"}
    if isinstance(type_ref, NullableTypeRef):
        return {"anyOf": [_schema_for_ref(type_ref.item, referenced), {"type": "null"}]}
    if isinstance(type_ref, ListTypeRef):
        return {"type": "array", "items": _schema_for_ref(type_ref.item, referenced)}
    if isinstance(type_ref, MapTypeRef):
        return {"type": "object", "additionalProperties": _schema_for_ref(type_ref.value, referenced)}
    raise TypeError(f"Unsupported type reference {type(type_ref).__name__}")


def _instructions(agent: AgentIR, ir: CanonicalIR) -> str:
    lines = [f"# {agent.name}", "", agent.goal]
    if agent.description:
        lines.extend(["", agent.description])
    guidance = [item.text for item in agent.guidance if "model" in item.audience]
    if guidance:
        lines.extend(["", "## Guidance", "", *(f"- {item}" for item in guidance)])
    edges = [
        edge
        for edge in ir.composition.values()
        if edge.source_agent_id == agent.id and "model" in edge.audience
    ]
    if edges:
        lines.extend(["", "## Available delegation", ""])
        for edge in edges:
            target = ir.agents[edge.target_agent_id]
            label = "Hand off to" if edge.mode == "handoff" else "Delegate to"
            lines.append(f"- {label} `{target.name}`: {edge.description}")
    model_controls = [
        control
        for control in ir.controls.values()
        if control.agent_id == agent.id and "model" in control.audience
    ]
    if model_controls:
        lines.extend(["", "## Required controls", ""])
        for control in model_controls:
            safe_summary = control.requirement or control.name.replace("_", " ")
            lines.append(f"- {safe_summary}")
    lines.extend(["", f"Return output conforming to `{format_type_ref(agent.output_type)}`."])
    return "\n".join(lines).strip() + "\n"


def _generated_docs(ir: CanonicalIR, digest: str) -> FrozenMap[PurePosixPath, str]:
    summary = [
        "# Generated Contract4Agents Summary",
        "",
        f"Contract digest: `{digest}`",
        "",
        "## Agents",
        "",
    ]
    for agent in ir.agents.values():
        summary.append(f"- [`{agent.name}`](agents/{agent.name}.md) -> `{format_type_ref(agent.output_type)}`")
    summary.extend(["", "## Capabilities", ""])
    for capability in ir.capabilities.values():
        summary.append(f"- `{capability.id}` ({capability.kind})")
    summary.extend(["", "## Controls", ""])
    for control in ir.controls.values():
        summary.append(
            f"- `{control.id}` ({'required' if control.required else 'advisory'}, {control.assessment})"
        )
    docs: list[tuple[PurePosixPath, str]] = [(PurePosixPath("summary.md"), "\n".join(summary) + "\n")]
    for agent in ir.agents.values():
        docs.append((PurePosixPath(f"agents/{agent.name}.md"), _agent_doc(agent, ir, digest)))
    return FrozenMap(docs)


def _agent_doc(agent: AgentIR, ir: CanonicalIR, digest: str) -> str:
    lines = [
        f"# {agent.name}",
        "",
        f"Semantic ID: `{agent.id}`  ",
        f"Contract digest: `{digest}`",
        "",
        "## Intent",
        "",
        agent.goal,
        "",
        "## Signature",
        "",
    ]
    for parameter in agent.parameters:
        lines.append(f"- `{parameter.name}: {format_type_ref(parameter.type_ref)}`")
    lines.append(f"- Returns `{format_type_ref(agent.output_type)}`")
    lines.extend(["", "## Grants", ""])
    grants = [ir.grants[item] for item in agent.grant_ids]
    if grants:
        for grant in grants:
            lines.append(
                f"- `{grant.capability_id}`: {grant.availability}, "
                f"{grant.authorization or 'not authorized'}, {grant.execution or 'unresolved'}"
            )
    else:
        lines.append("None.")
    lines.extend(["", "## Composition", ""])
    edges = [edge for edge in ir.composition.values() if edge.source_agent_id == agent.id]
    if edges:
        for edge in edges:
            lines.append(f"- `{edge.id}` -> `{edge.target_agent_id}` ({edge.mode})")
    else:
        lines.append("None.")
    lines.extend(["", "## Controls and quality", ""])
    for control in ir.controls.values():
        if control.agent_id == agent.id:
            lines.append(f"- Control `{control.id}` ({control.assessment})")
    for quality in ir.qualities.values():
        if quality.agent_id == agent.id:
            lines.append(f"- Quality `{quality.id}`")
    return "\n".join(lines).rstrip() + "\n"


def _plain_json(value: object) -> object:
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    return value


def _digest_json(value: object) -> str:
    source = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return _digest_text(source)


def _digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


__all__ = [
    "CompilerArtifacts",
    "artifact_digests",
    "build_artifacts",
    "compile_project",
]
