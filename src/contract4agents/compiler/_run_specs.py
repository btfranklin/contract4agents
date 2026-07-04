"""Run spec artifact generation."""

from __future__ import annotations

from contract4agents.ast import ContractProject, RunSpecDef
from contract4agents.compiler._types import RunSpecArtifact, RunSpecDerivedValue, RunSpecStage
from contract4agents.run_specs import (
    normalize_derived_value_type,
    parse_run_spec_derived_value_declaration,
    parse_run_spec_stage_declaration,
)


def run_spec_artifact(run_spec: RunSpecDef, project: ContractProject) -> RunSpecArtifact:
    return {
        "name": run_spec.name,
        "source_path": _source_path(run_spec, project),
        "stages": [_stage_artifact(raw_stage) for raw_stage in run_spec.stages],
        "derived_values": [
            _derived_value_artifact(raw_value) for raw_value in run_spec.attributes.get("derived_values", [])
        ],
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


def _derived_value_artifact(raw_value: str) -> RunSpecDerivedValue:
    declaration = parse_run_spec_derived_value_declaration(raw_value)
    if declaration is None:
        raise ValueError(f"Invalid run spec derived value declaration after semantic analysis: {raw_value}")
    normalized_type = normalize_derived_value_type(declaration.type_name)
    if normalized_type is None:
        raise ValueError(f"Invalid run spec derived value type after semantic analysis: {raw_value}")
    return {"name": declaration.name, "type": normalized_type}


def _source_path(run_spec: RunSpecDef, project: ContractProject) -> str:
    try:
        return str(run_spec.span.path.relative_to(project.root))
    except ValueError:
        return str(run_spec.span.path)


__all__ = ["run_spec_artifact"]
