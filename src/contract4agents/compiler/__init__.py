"""Provider-neutral compiler for Contract4Agents projects."""

from __future__ import annotations

from pathlib import Path

from contract4agents.ast import ContractProject
from contract4agents.compiler._capabilities import adapter_capability_matrix
from contract4agents.compiler._docs import generated_docs
from contract4agents.compiler._instructions import agent_instructions
from contract4agents.compiler._manifests import agent_manifest, eval_pack, monitor_pack
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
    TypeBinding,
)
from contract4agents.compiler._writer import write_artifacts
from contract4agents.diagnostics import ContractError, Diagnostic, raise_if_errors
from contract4agents.guards import GuardPlanItem, build_guard_plan
from contract4agents.parser import parse_project
from contract4agents.semantics import analyze_project

SOURCE_OWNED_OUTPUT_DIRS = frozenset(
    {
        "agents",
        "datasources",
        "docs",
        "evals",
        "examples",
        "monitors",
        "src",
        "tests",
        "types",
    }
)


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
        output_path = Path(output_dir)
        _validate_output_dir(Path(root), output_path)
        write_artifacts(artifacts, output_path, check=check)
    return artifacts


def _validate_output_dir(root: Path, output_dir: Path) -> None:
    root_path = root.resolve()
    output_path = output_dir.resolve()
    if output_path == root_path:
        _raise_unsafe_output_dir(output_dir, "it is the project root")
    if output_path.parent == root_path and output_path.name in SOURCE_OWNED_OUTPUT_DIRS:
        _raise_unsafe_output_dir(output_dir, f"`{output_path.name}` is a source-owned project directory")


def _raise_unsafe_output_dir(output_dir: Path, reason: str) -> None:
    raise ContractError(
        [
            Diagnostic(
                "COMPILE002",
                f"Refusing to write compiler artifacts to `{output_dir}` because {reason}.",
                hint="Choose a generated artifact directory such as `.contract/build`.",
            )
        ]
    )


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
