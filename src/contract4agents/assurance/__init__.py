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
from contract4agents.assurance._inputs import (
    RUN_SPEC_ASSESSMENT_INPUT_VERSION,
    RunSpecAssessmentInput,
    RunSpecAssessmentManifest,
)
from contract4agents.assurance._models import (
    AssessmentClassification,
    AssessorIdentity,
    AssuranceStatus,
    ControlApplicability,
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
from contract4agents.assurance._workflow import assess_assurance_evidence

__all__ = [
    "BUNDLE_VERSION",
    "AssuranceBundle",
    "AssessmentClassification",
    "AssessorIdentity",
    "AssuranceStatus",
    "BundleDiagnostic",
    "ControlApplicability",
    "ControlResult",
    "DiffArea",
    "DiffChange",
    "DiffImpact",
    "SemanticDiff",
    "SemanticDiffEntry",
    "RUN_SPEC_ASSESSMENT_INPUT_VERSION",
    "RunSpecAssertionResult",
    "RunSpecAssessmentInput",
    "RunSpecAssessmentManifest",
    "RunSpecEvidence",
    "RunSpecEvidenceStatus",
    "RunSpecResult",
    "RunSpecSelection",
    "RunSpecStageObservation",
    "RunSpecStageResult",
    "assemble_assurance_bundle",
    "assess_controls",
    "assess_assurance_evidence",
    "assess_run_spec",
    "diff_contracts",
    "diff_materialization_plans",
    "semantic_diff",
    "verify_assurance_bundle",
    "write_assurance_bundle",
]
