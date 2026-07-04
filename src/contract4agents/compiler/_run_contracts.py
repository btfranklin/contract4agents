"""Run-contract artifact generation."""

from __future__ import annotations

from contract4agents.ast import ContractProject, RunContractDef
from contract4agents.compiler._types import RunContractArtifact, RunContractStage
from contract4agents.run_contracts import parse_run_stage_declaration


def run_contract_artifact(run_contract: RunContractDef, project: ContractProject) -> RunContractArtifact:
    return {
        "name": run_contract.name,
        "source_path": _source_path(run_contract, project),
        "stages": [_stage_artifact(raw_stage) for raw_stage in run_contract.stages],
        "assertions": list(run_contract.assertions),
    }


def _stage_artifact(raw_stage: str) -> RunContractStage:
    stage = parse_run_stage_declaration(raw_stage)
    if stage is None:
        raise ValueError(f"Invalid run stage declaration after semantic analysis: {raw_stage}")
    return {
        "name": stage.name,
        "agent": stage.agent,
        "output_type": stage.output_type,
        "cardinality": stage.cardinality,
        "manifest_ref": f"manifests/{stage.agent}.json",
        "schema_ref": f"schemas/{stage.output_type}.json",
    }


def _source_path(run_contract: RunContractDef, project: ContractProject) -> str:
    try:
        return str(run_contract.span.path.relative_to(project.root))
    except ValueError:
        return str(run_contract.span.path)


__all__ = ["run_contract_artifact"]
