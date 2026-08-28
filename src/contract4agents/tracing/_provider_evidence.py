"""Content-free, provider-neutral outcome and usage evidence.

The types in this module are deliberately small.  Provider adapters may inspect
their native objects, but only these allowlisted facts cross the normalized
trace boundary.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from contract4agents.ir import SemanticId

EvidenceState = Literal["observed", "inferred", "unavailable", "unverified"]
ProviderOutcomePhase = Literal[
    "transport", "request", "response", "structured_output", "cancellation"
]
ProviderOutcome = Literal["succeeded", "failed", "refused", "cancelled", "unknown"]
ProviderOutcomeCategory = Literal[
    "transport",
    "rate_limit",
    "authentication",
    "authorization",
    "provider_timeout",
    "refusal",
    "structured_output",
    "cancelled",
    "provider_error",
    "unknown",
]
ProviderUsageScope = Literal["response", "model_call", "attempt", "run"]
ProviderUsageCoverage = Literal["complete", "partial", "unavailable", "unverified"]

_STATES = frozenset({"observed", "inferred", "unavailable", "unverified"})
_PHASES = frozenset({"transport", "request", "response", "structured_output", "cancellation"})
_OUTCOMES = frozenset({"succeeded", "failed", "refused", "cancelled", "unknown"})
_CATEGORIES = frozenset(
    {
        "transport",
        "rate_limit",
        "authentication",
        "authorization",
        "provider_timeout",
        "refusal",
        "structured_output",
        "cancelled",
        "provider_error",
        "unknown",
    }
)
_SCOPES = frozenset({"response", "model_call", "attempt", "run"})
_COVERAGE = frozenset({"complete", "partial", "unavailable", "unverified"})
_SAFE_CODE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(frozen=True)
class ProviderOutcomeEvidence:
    """One content-free provider outcome claim bound to an agent attempt."""

    agent_id: SemanticId | str
    attempt_id: str
    phase: ProviderOutcomePhase
    outcome: ProviderOutcome
    category: ProviderOutcomeCategory
    state: EvidenceState
    classifier_provenance: str
    invocation_id: str | None = None
    attempt_number: int | None = None
    http_status: int | None = None
    provider_error_code: str | None = None
    request_id: str | None = None
    response_id: str | None = None
    retry_after_seconds: float | None = None
    response_received: bool | None = None

    def __post_init__(self) -> None:
        if isinstance(self.agent_id, str):
            object.__setattr__(self, "agent_id", SemanticId.parse(self.agent_id))
        if not isinstance(self.agent_id, SemanticId):
            raise TypeError("provider outcome agent_id must be a semantic ID")
        self.agent_id.require_kind("agent")
        _safe_identifier("attempt_id", self.attempt_id)
        _bounded_text("classifier_provenance", self.classifier_provenance)
        if self.invocation_id is not None:
            _safe_identifier("invocation_id", self.invocation_id)
        if self.attempt_number is not None:
            _nonnegative_int("attempt_number", self.attempt_number)
            if self.attempt_number < 1:
                raise ValueError("attempt_number must be at least one")
        _enum("phase", self.phase, _PHASES)
        _enum("outcome", self.outcome, _OUTCOMES)
        _enum("category", self.category, _CATEGORIES)
        _enum("state", self.state, _STATES)
        if self.http_status is not None:
            _nonnegative_int("http_status", self.http_status)
            if self.http_status > 999:
                raise ValueError("http_status must be at most 999")
        if self.provider_error_code is not None:
            _text("provider_error_code", self.provider_error_code)
            if _SAFE_CODE.fullmatch(self.provider_error_code) is None:
                raise ValueError("provider_error_code contains unsupported content")
        for name in ("request_id", "response_id"):
            value = getattr(self, name)
            if value is not None:
                _safe_identifier(name, value)
        if self.retry_after_seconds is not None:
            if isinstance(self.retry_after_seconds, bool) or not isinstance(self.retry_after_seconds, int | float):
                raise TypeError("retry_after_seconds must be numeric")
            if self.retry_after_seconds < 0 or not math.isfinite(self.retry_after_seconds):
                raise ValueError("retry_after_seconds must be finite and non-negative")
        if self.outcome == "refused" and self.category != "refusal":
            raise ValueError("A refused outcome requires the refusal category")
        if self.outcome == "cancelled" and self.category != "cancelled":
            raise ValueError("A cancelled outcome requires the cancelled category")
        if self.outcome == "succeeded" and self.category in {
            "authentication",
            "authorization",
            "cancelled",
            "provider_error",
            "rate_limit",
            "refusal",
        }:
            raise ValueError("A succeeded outcome cannot use a failure category")

    @property
    def claim_state(self) -> EvidenceState:
        return self.state

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "agent_id": str(self.agent_id),
            "attempt_id": self.attempt_id,
            "category": self.category,
            "classifier_provenance": self.classifier_provenance,
            "outcome": self.outcome,
            "phase": self.phase,
            "state": self.state,
        }
        for name in (
            "attempt_number",
            "http_status",
            "invocation_id",
            "provider_error_code",
            "request_id",
            "response_id",
            "retry_after_seconds",
            "response_received",
        ):
            result[name] = getattr(self, name)
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)

    @classmethod
    def from_dict(cls, value: object) -> ProviderOutcomeEvidence:
        payload = _object(value, "provider outcome")
        required = {"agent_id", "attempt_id", "category", "classifier_provenance", "outcome", "phase", "state"}
        optional = {
            "attempt_number",
            "http_status",
            "invocation_id",
            "provider_error_code",
            "request_id",
            "response_id",
            "retry_after_seconds",
            "response_received",
        }
        _keys(payload, required, optional, "provider outcome")
        return cls(**payload)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ProviderUsageEvidence:
    """Content-free provider token and request usage for one aggregation."""

    scope: ProviderUsageScope
    coverage: ProviderUsageCoverage
    aggregation_identity: str
    aggregation_basis: str
    provenance: str
    request_count: int | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    agent_id: SemanticId | str | None = None
    attempt_id: str | None = None
    invocation_id: str | None = None

    def __post_init__(self) -> None:
        _enum("scope", self.scope, _SCOPES)
        _enum("coverage", self.coverage, _COVERAGE)
        _safe_identifier("aggregation_identity", self.aggregation_identity)
        _bounded_text("aggregation_basis", self.aggregation_basis)
        _bounded_text("provenance", self.provenance)
        for name in (
            "request_count",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
        ):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_int(name, value)
        if (
            self.cached_input_tokens is not None
            and self.input_tokens is not None
            and self.cached_input_tokens > self.input_tokens
        ):
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        known = [
            self.request_count,
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.total_tokens,
        ]
        if self.total_tokens is not None and self.input_tokens is not None and self.output_tokens is not None:
            expected = self.input_tokens + self.output_tokens
            if self.total_tokens != expected:
                raise ValueError("total_tokens must equal input_tokens + output_tokens")
        if (
            self.reasoning_tokens is not None
            and self.output_tokens is not None
            and self.reasoning_tokens > self.output_tokens
        ):
            raise ValueError("reasoning_tokens cannot exceed output_tokens")
        if self.coverage == "complete" and any(
            value is None for value in (self.input_tokens, self.output_tokens, self.total_tokens)
        ):
            raise ValueError("complete usage requires input, output, and total token facts")
        if self.coverage == "unavailable" and any(value is not None for value in known):
            raise ValueError("unavailable usage cannot contain numeric facts")
        if self.agent_id is not None:
            if isinstance(self.agent_id, str):
                object.__setattr__(self, "agent_id", SemanticId.parse(self.agent_id))
            if not isinstance(self.agent_id, SemanticId):
                raise TypeError("provider usage agent_id must be a semantic ID")
            self.agent_id.require_kind("agent")
        for name in ("attempt_id", "invocation_id"):
            value = getattr(self, name)
            if value is not None:
                _safe_identifier(name, value)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "aggregation_basis": self.aggregation_basis,
            "aggregation_identity": self.aggregation_identity,
            "coverage": self.coverage,
            "provenance": self.provenance,
            "scope": self.scope,
        }
        for name in (
            "agent_id",
            "attempt_id",
            "cached_input_tokens",
            "input_tokens",
            "invocation_id",
            "output_tokens",
            "reasoning_tokens",
            "request_count",
            "total_tokens",
        ):
            value = getattr(self, name)
            result[name] = str(value) if isinstance(value, SemanticId) else value
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)

    @classmethod
    def from_dict(cls, value: object) -> ProviderUsageEvidence:
        payload = _object(value, "provider usage")
        required = {"aggregation_basis", "aggregation_identity", "coverage", "provenance", "scope"}
        optional = {
            "agent_id",
            "attempt_id",
            "cached_input_tokens",
            "input_tokens",
            "invocation_id",
            "output_tokens",
            "reasoning_tokens",
            "request_count",
            "total_tokens",
        }
        _keys(payload, required, optional, "provider usage")
        return cls(**payload)  # type: ignore[arg-type]


def provider_outcome_event_data(evidence: ProviderOutcomeEvidence) -> dict[str, object]:
    """Return the only normalized payload accepted for an outcome report."""

    return {"evidence": evidence.to_dict()}


def provider_usage_event_data(evidence: ProviderUsageEvidence) -> dict[str, object]:
    """Return the only normalized payload accepted for a usage report."""

    return {"evidence": evidence.to_dict()}


def _text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _safe_identifier(name: str, value: object) -> None:
    _text(name, value)
    assert isinstance(value, str)
    if len(value) > 256 or any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError(f"{name} contains unsupported content")


def _bounded_text(name: str, value: object) -> None:
    _text(name, value)
    assert isinstance(value, str)
    if len(value) > 256 or any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} contains unsupported content")


def _nonnegative_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _enum(name: str, value: object, values: frozenset[str]) -> None:
    if value not in values:
        raise ValueError(f"Unsupported {name} `{value}`")


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"{label} must be an object")
    return dict(value)


def _keys(payload: dict[str, object], required: set[str], optional: set[str], label: str) -> None:
    missing = sorted(required - payload.keys())
    unknown = sorted(payload.keys() - required - optional)
    if missing:
        raise ValueError(f"{label} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")


__all__ = [
    "EvidenceState",
    "ProviderOutcome",
    "ProviderOutcomeCategory",
    "ProviderOutcomeEvidence",
    "ProviderOutcomePhase",
    "ProviderUsageCoverage",
    "ProviderUsageEvidence",
    "ProviderUsageScope",
    "provider_outcome_event_data",
    "provider_usage_event_data",
]
