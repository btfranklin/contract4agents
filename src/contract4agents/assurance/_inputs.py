"""Strict host-evidence inputs for CLI run-spec assessment."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

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
        payload = _object("run-spec assessment input", value)
        _keys("run-spec assessment input", payload, {"evidence", "selection"})
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
        payload = _object("run-spec assessment manifest", value)
        _keys("run-spec assessment manifest", payload, {"runs", "version"})
        version = payload["version"]
        if not isinstance(version, str):
            raise TypeError("version must be a string")
        return cls(
            runs=tuple(RunSpecAssessmentInput.from_dict(item) for item in _array("runs", payload["runs"])),
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


def _object(label: str, value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"{label} must be an object with string keys")
    return cast(Mapping[str, object], value)


def _array(label: str, value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array")
    return value


def _keys(label: str, payload: Mapping[str, object], required: set[str]) -> None:
    missing = sorted(required - set(payload))
    unknown = sorted(set(payload) - required)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise ValueError(f"Invalid {label} keys: {'; '.join(details)}")


__all__ = [
    "RUN_SPEC_ASSESSMENT_INPUT_VERSION",
    "RunSpecAssessmentInput",
    "RunSpecAssessmentManifest",
]
