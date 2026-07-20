"""Immutable, deterministic assurance result values."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

AssuranceStatus = Literal["passed", "violated", "unverified"]
ControlApplicability = Literal["applicable", "not_applicable", "unverified"]
AssessmentClassification = Literal[
    "static",
    "adapter",
    "runtime",
    "host_attested",
    "post_run",
    "semantic",
    "advisory",
]

_ASSURANCE_STATUSES = frozenset({"passed", "violated", "unverified"})
_ASSESSMENT_CLASSIFICATIONS = frozenset(
    {
        "static",
        "adapter",
        "runtime",
        "host_attested",
        "post_run",
        "semantic",
        "advisory",
    }
)


@dataclass(frozen=True)
class AssessorIdentity:
    """Identity of the component that produced an assurance result."""

    name: str
    version: str

    def __post_init__(self) -> None:
        _require_text("assessor name", self.name)
        _require_text("assessor version", self.version)

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version}


@dataclass(frozen=True)
class ControlResult:
    """One contract control assessment with links to supporting evidence."""

    control_id: str
    status: AssuranceStatus
    reason: str
    assessment: AssessmentClassification
    assessor: AssessorIdentity
    applicability: ControlApplicability = "applicable"
    evidence_event_ids: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text("control_id", self.control_id)
        _require_text("reason", self.reason)
        if self.status not in _ASSURANCE_STATUSES:
            raise ValueError(f"Unsupported assurance status `{self.status}`")
        if self.applicability not in {"applicable", "not_applicable", "unverified"}:
            raise ValueError(f"Unsupported control applicability `{self.applicability}`")
        if self.assessment not in _ASSESSMENT_CLASSIFICATIONS:
            raise ValueError(f"Unsupported assessment classification `{self.assessment}`")
        object.__setattr__(
            self,
            "evidence_event_ids",
            _normalized_references("Evidence event ID", self.evidence_event_ids),
        )
        object.__setattr__(self, "evidence_refs", _normalized_references("Evidence reference", self.evidence_refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment": self.assessment,
            "assessor": self.assessor.to_dict(),
            "applicability": self.applicability,
            "control_id": self.control_id,
            "evidence_event_ids": list(self.evidence_event_ids),
            "evidence_refs": list(self.evidence_refs),
            "reason": self.reason,
            "status": self.status,
        }

    def to_json(self) -> str:
        return _deterministic_json(self.to_dict())


def _normalized_references(label: str, values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
        normalized.add(value)
    return tuple(sorted(normalized))


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label.capitalize()} must be a non-empty string")


def _deterministic_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


__all__ = [
    "AssessmentClassification",
    "AssessorIdentity",
    "AssuranceStatus",
    "ControlApplicability",
    "ControlResult",
]
