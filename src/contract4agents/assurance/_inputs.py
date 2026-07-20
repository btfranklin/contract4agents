"""Strict host-evidence inputs for CLI run-spec assessment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from contract4agents._strict_json import json_array, json_object, require_exact_keys
from contract4agents.assurance._run_specs import RunSpecEvidence, RunSpecSelection

RUN_SPEC_ASSESSMENT_INPUT_VERSION = "1"


@dataclass(frozen=True)
class RunSpecAssessmentInput:
    """One host-selected run spec and its raw evidence for one trace run."""

    selection: RunSpecSelection
    evidence: RunSpecEvidence | None

    def __post_init__(self) -> None:
        if self.selection.run_spec_id is None and self.evidence is not None:
            raise ValueError("A null run-spec selection cannot carry assessment evidence")
        if self.selection.run_spec_id is not None and self.evidence is None:
            raise ValueError("A selected run spec requires assessment evidence")

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
            "selection": self.selection.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> RunSpecAssessmentInput:
        payload = json_object("run-spec assessment input", value)
        require_exact_keys("run-spec assessment input", payload, {"evidence", "selection"})
        evidence = payload["evidence"]
        return cls(
            selection=RunSpecSelection.from_dict(payload["selection"]),
            evidence=None if evidence is None else RunSpecEvidence.from_dict(evidence),
        )


@dataclass(frozen=True)
class RunSpecAssessmentManifest:
    """Versioned atomic assessment input for all runs in one CLI invocation."""

    runs: tuple[RunSpecAssessmentInput, ...]
    version: str = RUN_SPEC_ASSESSMENT_INPUT_VERSION

    def __post_init__(self) -> None:
        if self.version != RUN_SPEC_ASSESSMENT_INPUT_VERSION:
            raise ValueError(
                f"Unsupported run-spec assessment input version `{self.version}`; "
                f"expected `{RUN_SPEC_ASSESSMENT_INPUT_VERSION}`"
            )
        run_ids = [item.selection.run_id for item in self.runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("Run-spec assessment inputs must have unique run_id values")

    def to_dict(self) -> dict[str, object]:
        return {"runs": [item.to_dict() for item in self.runs], "version": self.version}

    @classmethod
    def from_dict(cls, value: object) -> RunSpecAssessmentManifest:
        payload = json_object("run-spec assessment manifest", value)
        require_exact_keys("run-spec assessment manifest", payload, {"runs", "version"})
        version = payload["version"]
        if not isinstance(version, str):
            raise TypeError("version must be a string")
        return cls(
            runs=tuple(RunSpecAssessmentInput.from_dict(item) for item in json_array("runs", payload["runs"])),
            version=version,
        )

    @classmethod
    def from_json(cls, source: str) -> RunSpecAssessmentManifest:
        try:
            value: object = json.loads(source)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid run-spec assessment input JSON: {exc}") from exc
        return cls.from_dict(value)

    @classmethod
    def load(cls, path: Path | str) -> RunSpecAssessmentManifest:
        return cls.from_json(Path(path).read_text())


__all__ = [
    "RUN_SPEC_ASSESSMENT_INPUT_VERSION",
    "RunSpecAssessmentInput",
    "RunSpecAssessmentManifest",
]
