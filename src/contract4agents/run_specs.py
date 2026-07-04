"""Shared helpers for run spec declarations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from contract4agents.ast import RunSpecStageCardinality

_RUN_STAGE_RE = re.compile(
    r"\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<suffix>[?+])?\s*:\s*"
    r"(?P<agent>[A-Za-z_][A-Za-z0-9_]*)\s*->\s*"
    r"(?P<output>[A-Za-z_][A-Za-z0-9_]*)\s*"
)


@dataclass(frozen=True)
class RunSpecStageDeclaration:
    name: str
    agent: str
    output_type: str
    cardinality: RunSpecStageCardinality
    raw: str


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


__all__ = ["RunSpecStageDeclaration", "parse_run_spec_stage_declaration"]
