"""Run spec artifact generation."""

from __future__ import annotations

from contract4agents.ast import ContractProject, RunSpecDef
from contract4agents.compiler._types import RunSpecArtifact, RunSpecStage
from contract4agents.run_specs import parse_run_spec_stage_declaration


def run_spec_artifact(run_spec: RunSpecDef, project: ContractProject) -> RunSpecArtifact:
    return {
        "name": run_spec.name,
        "source_path": _source_path(run_spec, project),
        "stages": [_stage_artifact(raw_stage) for raw_stage in run_spec.stages],
        "assertions": list(run_spec.assertions),
    }


def _stage_artifact(raw_stage: str) -> RunSpecStage:
    stage = parse_run_spec_stage_declaration(raw_stage)
    if stage is None:
        raise ValueError(f"Invalid run spec stage declaration after semantic analysis: {raw_stage}")
    return {
        "name": stage.name,
        "agent": stage.agent,
        "output_type": stage.output_type,
        "cardinality": stage.cardinality,
        "manifest_ref": f"manifests/{stage.agent}.json",
        "schema_ref": f"schemas/{stage.output_type}.json",
    }


def _source_path(run_spec: RunSpecDef, project: ContractProject) -> str:
    try:
        return str(run_spec.span.path.relative_to(project.root))
    except ValueError:
        return str(run_spec.span.path)


__all__ = ["run_spec_artifact"]
