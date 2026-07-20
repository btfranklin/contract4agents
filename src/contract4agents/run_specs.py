"""Shared helpers for run spec declarations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from contract4agents.ast import RunSpecStageCardinality

_RUN_STAGE_RE = re.compile(
    r"\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<suffix>[?+])?\s*:\s*"
    r"(?P<agent>[A-Za-z_][A-Za-z0-9_]*)\s*->\s*"
    r"(?P<output>[A-Za-z_][A-Za-z0-9_]*)\s*"
)
_RUN_DERIVED_VALUE_RE = re.compile(
    r"\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<type>.+?)\s*"
)
DERIVED_VALUE_SCALAR_TYPES = frozenset({"string", "integer", "float", "boolean"})


@dataclass(frozen=True)
class RunSpecStageDeclaration:
    name: str
    agent: str
    output_type: str
    cardinality: RunSpecStageCardinality
    raw: str


@dataclass(frozen=True)
class RunSpecDerivedValueDeclaration:
    name: str
    type_name: str
    raw: str


@dataclass(frozen=True)
class RunSpecStageSourceComponent:
    role: Literal["stage", "agent", "output"]
    value: str
    start: int
    end: int


def parse_run_spec_stage_declaration(value: str) -> RunSpecStageDeclaration | None:
    match = _RUN_STAGE_RE.fullmatch(value)
    if match is None:
        return None
    suffix = match.group("suffix")
    cardinality: RunSpecStageCardinality
    if suffix == "?":
        cardinality = "optional"
    elif suffix == "+":
        cardinality = "many"
    else:
        cardinality = "one"
    return RunSpecStageDeclaration(
        name=match.group("name"),
        agent=match.group("agent"),
        output_type=match.group("output"),
        cardinality=cardinality,
        raw=value,
    )


def run_spec_stage_source_components(value: str) -> tuple[RunSpecStageSourceComponent, ...]:
    """Return positioned stage components using the canonical stage syntax."""

    match = _RUN_STAGE_RE.fullmatch(value)
    if match is None:
        return ()
    return (
        RunSpecStageSourceComponent("stage", match.group("name"), *match.span("name")),
        RunSpecStageSourceComponent("agent", match.group("agent"), *match.span("agent")),
        RunSpecStageSourceComponent("output", match.group("output"), *match.span("output")),
    )


def parse_run_spec_derived_value_declaration(value: str) -> RunSpecDerivedValueDeclaration | None:
    match = _RUN_DERIVED_VALUE_RE.fullmatch(value)
    if match is None:
        return None
    return RunSpecDerivedValueDeclaration(
        name=match.group("name"),
        type_name=_compact_type_name(match.group("type")),
        raw=value,
    )


def normalize_derived_value_type(type_name: str) -> str | None:
    value = _compact_type_name(type_name)
    if value in DERIVED_VALUE_SCALAR_TYPES:
        return value
    if value.startswith("list[") and value.endswith("]"):
        member_type = value[5:-1]
        if member_type in DERIVED_VALUE_SCALAR_TYPES:
            return f"list[{member_type}]"
    return None


def derived_value_collection_member_type(type_name: str) -> str | None:
    normalized = normalize_derived_value_type(type_name)
    if normalized is None or not normalized.startswith("list[") or not normalized.endswith("]"):
        return None
    return normalized[5:-1]


def _compact_type_name(type_name: str) -> str:
    return re.sub(r"\s+", "", type_name.strip())


__all__ = [
    "DERIVED_VALUE_SCALAR_TYPES",
    "RunSpecDerivedValueDeclaration",
    "RunSpecStageDeclaration",
    "RunSpecStageSourceComponent",
    "derived_value_collection_member_type",
    "normalize_derived_value_type",
    "parse_run_spec_derived_value_declaration",
    "parse_run_spec_stage_declaration",
    "run_spec_stage_source_components",
]
