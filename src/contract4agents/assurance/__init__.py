"""Provider-neutral assurance result models."""

from contract4agents.assurance._assess import assess_controls
from contract4agents.assurance._bundle import (
    BUNDLE_VERSION,
    AssuranceBundle,
    BundleDiagnostic,
    assemble_assurance_bundle,
    verify_assurance_bundle,
    write_assurance_bundle,
)
from contract4agents.assurance._diff import (
    DiffArea,
    DiffChange,
    DiffImpact,
    SemanticDiff,
    SemanticDiffEntry,
    diff_contracts,
    diff_materialization_plans,
    semantic_diff,
)
from contract4agents.assurance._models import (
    AssessmentClassification,
    AssessorIdentity,
    AssuranceStatus,
    ControlResult,
)
from contract4agents.assurance._run_specs import (
    RunSpecAssertionResult,
    RunSpecEvidence,
    RunSpecEvidenceStatus,
    RunSpecResult,
    RunSpecSelection,
    RunSpecStageObservation,
    RunSpecStageResult,
    assess_run_spec,
)

__all__ = [
    "BUNDLE_VERSION",
    "AssuranceBundle",
    "AssessmentClassification",
    "AssessorIdentity",
    "AssuranceStatus",
    "BundleDiagnostic",
    "ControlResult",
    "DiffArea",
    "DiffChange",
    "DiffImpact",
    "SemanticDiff",
    "SemanticDiffEntry",
    "RunSpecAssertionResult",
    "RunSpecEvidence",
    "RunSpecEvidenceStatus",
    "RunSpecResult",
    "RunSpecSelection",
    "RunSpecStageObservation",
    "RunSpecStageResult",
    "assemble_assurance_bundle",
    "assess_controls",
    "assess_run_spec",
    "diff_contracts",
    "diff_materialization_plans",
    "semantic_diff",
    "verify_assurance_bundle",
    "write_assurance_bundle",
]
