"""Helpers for safe, deterministic native configuration evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Final

from contract4agents.ir import FrozenJsonValue, FrozenMap, SemanticId, freeze_json
from contract4agents.materialization._models import (
    ConfigurationConformanceEvidence,
    ConfigurationObservationSource,
    ConfigurationStatus,
)

MISSING: Final = object()

# These options are deliberately limited to stable scalar values which cannot
# contain credentials or provider request payloads.  Other options are still
# checked, but their evidence contains only a digest.
SAFE_OPTION_PATHS: Final = frozenset(
    {
        "store",
        "retry.max_retries",
        "reasoning.effort",
        "temperature",
        "top_p",
        "max_tokens",
        "parallel_tool_calls",
    }
)


def flatten_mapping(values: Mapping[str, object], prefix: str = "") -> tuple[tuple[str, object], ...]:
    """Flatten a JSON mapping while preserving explicit null and omission."""

    result: list[tuple[str, object]] = []
    for key in sorted(values):
        path = f"{prefix}.{key}" if prefix else str(key)
        value = values[key]
        if isinstance(value, Mapping):
            result.extend(flatten_mapping(value, path))
        else:
            result.append((path, value))
    return tuple(result)


def read_public_path(value: object, path: str) -> object:
    """Read a public dotted property path, never traversing private names."""

    current = value
    for part in path.split("."):
        if not part or part.startswith("_"):
            return MISSING
        try:
            if isinstance(current, Mapping):
                current = current[part]
            else:
                current = getattr(current, part)
        except (AttributeError, KeyError, TypeError):
            return MISSING
    return current


def configuration_evidence(
    semantic_id: SemanticId,
    property_path: str,
    planned: object,
    observed: object,
    *,
    source: ConfigurationObservationSource = "native_readback",
    required: bool = True,
    safe: bool = True,
    reason: str | None = None,
) -> ConfigurationConformanceEvidence:
    """Create one record and redact arbitrary values before serialization."""

    planned_present = planned is not MISSING
    observed_present = observed is not MISSING
    planned_value = None if not planned_present or not safe else planned
    observed_value = None if not observed_present or not safe else observed
    planned_digest = _digest(planned_present, freeze_json(planned) if planned_present else None)
    observed_digest = _digest(observed_present, freeze_json(observed) if observed_present else None)
    if not planned_present or not observed_present:
        status: ConfigurationStatus = "unverified" if planned_present != observed_present else "passed"
    elif planned_digest != observed_digest:
        status = "violated"
    else:
        status = "passed"
    if reason is None and status == "unverified":
        reason = (
            "Required native configuration property could not be observed."
            if required
            else "Native configuration property could not be observed."
        )
    return ConfigurationConformanceEvidence(
        semantic_id=semantic_id,
        property_path=property_path,
        planned_value=planned_value,
        observed_value=observed_value,
        status=status,
        observation_source=source,
        reason=reason,
        required=required,
        planned_present=planned_present,
        observed_present=observed_present,
        planned_digest=planned_digest,
        observed_digest=observed_digest,
        planned_redacted=not safe,
        observed_redacted=not safe,
    )


def digest_only_configuration_evidence(
    semantic_id: SemanticId,
    property_path: str,
    planned: object,
    observed: object,
    *,
    source: ConfigurationObservationSource = "native_readback",
    required: bool = True,
    reason: str | None = None,
) -> ConfigurationConformanceEvidence:
    return configuration_evidence(
        semantic_id,
        property_path,
        planned,
        observed,
        source=source,
        required=required,
        safe=False,
        reason=reason,
    )


def _digest(present: bool, value: FrozenJsonValue | None) -> str:
    payload = {"present": present, "value": _thaw(value)}
    source = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(source.encode('utf-8')).hexdigest()}"


def _thaw(value: FrozenJsonValue | None) -> object:
    if isinstance(value, FrozenMap):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


__all__ = [
    "MISSING",
    "SAFE_OPTION_PATHS",
    "configuration_evidence",
    "digest_only_configuration_evidence",
    "flatten_mapping",
    "read_public_path",
]
