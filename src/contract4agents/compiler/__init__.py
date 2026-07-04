"""Provider-neutral compiler for Contract4Agents projects."""

from __future__ import annotations

from pathlib import Path

from contract4agents.ast import ContractProject
from contract4agents.compiler._capabilities import adapter_capability_matrix
from contract4agents.compiler._docs import generated_docs
from contract4agents.compiler._instructions import agent_instructions
from contract4agents.compiler._manifests import agent_manifest, eval_pack, monitor_pack
from contract4agents.compiler._run_specs import run_spec_artifact
from contract4agents.compiler._schemas import build_type_artifacts
from contract4agents.compiler._types import (
    AgentManifest,
    CapabilityEntry,
    CapabilityMatrix,
    CompilerArtifacts,
    EvalPack,
    JsonSchema,
    ManifestDatasource,
    ManifestHostContext,
    ManifestHostedTool,
    ManifestInput,
    ManifestOutput,
    ManifestUse,
    MonitorPack,
    RunSpecArtifact,
    RunSpecStage,
    TypeBinding,
)
from contract4agents.compiler._writer import write_artifacts
from contract4agents.diagnostics import raise_if_errors
from contract4agents.guards import GuardPlanItem, build_guard_plan
from contract4agents.output_paths import validate_output_dir
from contract4agents.parser import parse_project
from contract4agents.semantics import analyze_project


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
        output_path = validate_output_dir(project.root, output_dir, artifact_label="compiler artifacts")
        write_artifacts(artifacts, output_path, check=check)
    return artifacts


def build_artifacts(project: ContractProject, allow_python_imports: bool = False) -> CompilerArtifacts:
    schemas, type_bindings = build_type_artifacts(project, allow_python_imports=allow_python_imports)
    manifests = {name: agent_manifest(agent, project) for name, agent in project.agents.items()}
    instructions = {name: agent_instructions(agent) for name, agent in project.agents.items()}
    eval_packs = [eval_pack(eval_case) for eval_case in project.evals]
    monitors = [monitor_pack(monitor) for monitor in project.monitors]
    run_specs = [
        run_spec_artifact(run_spec, project)
        for run_spec in sorted(project.run_specs.values(), key=lambda item: item.name)
    ]
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
        "run_specs": run_specs,
        "guard_plan": guard_plan,
        "adapter_capability_matrix": capability_matrix,
        "docs": docs,
    }


__all__ = [
    "AgentManifest",
    "CapabilityEntry",
    "CapabilityMatrix",
    "CompilerArtifacts",
    "EvalPack",
    "GuardPlanItem",
    "JsonSchema",
    "ManifestHostedTool",
    "ManifestHostContext",
    "ManifestDatasource",
    "ManifestInput",
    "ManifestOutput",
    "ManifestUse",
    "MonitorPack",
    "RunSpecArtifact",
    "RunSpecStage",
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
    "run_spec_artifact",
    "write_artifacts",
]
