"""Shared datasource satisfiability and selection helpers."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Literal

from contract4agents.type_refs import canonical_type_name

DatasourceResolutionStatus = Literal["ok", "missing", "ambiguous", "cycle", "denied"]


@dataclass(frozen=True)
class DatasourceCandidate:
    name: str
    produces: str
    requires: tuple[str, ...]


@dataclass(frozen=True)
class DatasourceResolution:
    status: DatasourceResolutionStatus
    type_name: str
    path: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    cycle: tuple[str, ...] = ()
    selected: str | None = None

    @classmethod
    def ok(cls, type_name: str, path: tuple[str, ...], selected: str | None = None) -> DatasourceResolution:
        return cls("ok", type_name, path, selected=selected)


def resolve_datasource_type(
    type_name: str,
    *,
    available: Collection[str],
    datasources: Iterable[DatasourceCandidate],
    allowed_datasources: Collection[str] | None = None,
) -> DatasourceResolution:
    """Resolve a type against available context and datasource candidates."""

    normalized_available = {canonical_type_name(item) for item in available}
    candidates = tuple(datasources)
    return _resolve_type(
        type_name,
        available=normalized_available,
        datasources=candidates,
        allowed_datasources=set(allowed_datasources) if allowed_datasources is not None else None,
        resolving=(),
        path=(),
    )


def _resolve_type(
    type_name: str,
    *,
    available: set[str],
    datasources: tuple[DatasourceCandidate, ...],
    allowed_datasources: set[str] | None,
    resolving: tuple[str, ...],
    path: tuple[str, ...],
) -> DatasourceResolution:
    normalized = canonical_type_name(type_name)
    if normalized in available:
        return DatasourceResolution.ok(normalized, (*path, normalized))
    if normalized in resolving:
        cycle_start = resolving.index(normalized)
        return DatasourceResolution("cycle", normalized, path, cycle=(*resolving[cycle_start:], normalized))

    all_candidates = [
        datasource for datasource in datasources if canonical_type_name(datasource.produces) == normalized
    ]
    candidates = _allowed_candidates(all_candidates, allowed_datasources)
    if all_candidates and not candidates:
        return DatasourceResolution("denied", normalized, path)
    if not candidates:
        return DatasourceResolution("missing", normalized, path)

    valid: list[DatasourceCandidate] = []
    first_failure: DatasourceResolution | None = None
    for candidate in candidates:
        proof = _resolve_datasource(
            candidate,
            available=available,
            datasources=datasources,
            allowed_datasources=allowed_datasources,
            resolving=(*resolving, normalized),
            path=path,
        )
        if proof.status == "ok":
            valid.append(candidate)
            continue
        if first_failure is None or (first_failure.status != "cycle" and proof.status == "cycle"):
            first_failure = proof

    if len(valid) > 1:
        return DatasourceResolution(
            "ambiguous",
            normalized,
            path,
            candidates=tuple(sorted(datasource.name for datasource in valid)),
        )
    if len(valid) == 1:
        selected = valid[0]
        return DatasourceResolution.ok(
            normalized,
            (*path, f"{selected.name}:{normalized}"),
            selected=selected.name,
        )
    return first_failure or DatasourceResolution("missing", normalized, path)


def _resolve_datasource(
    datasource: DatasourceCandidate,
    *,
    available: set[str],
    datasources: tuple[DatasourceCandidate, ...],
    allowed_datasources: set[str] | None,
    resolving: tuple[str, ...],
    path: tuple[str, ...],
) -> DatasourceResolution:
    datasource_path = (*path, datasource.name)
    for required in datasource.requires:
        proof = _resolve_type(
            required,
            available=available,
            datasources=datasources,
            allowed_datasources=allowed_datasources,
            resolving=resolving,
            path=datasource_path,
        )
        if proof.status != "ok":
            return proof
    return DatasourceResolution.ok(datasource.produces, datasource_path, selected=datasource.name)


def _allowed_candidates(
    candidates: list[DatasourceCandidate],
    allowed_datasources: set[str] | None,
) -> list[DatasourceCandidate]:
    if allowed_datasources is None:
        return candidates
    return [candidate for candidate in candidates if candidate.name in allowed_datasources]


__all__ = [
    "DatasourceCandidate",
    "DatasourceResolution",
    "DatasourceResolutionStatus",
    "resolve_datasource_type",
]
