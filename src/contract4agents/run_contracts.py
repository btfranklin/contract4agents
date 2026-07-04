"""Shared helpers for run-contract declarations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from contract4agents.ast import RunStageCardinality

_RUN_STAGE_RE = re.compile(
    r"\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<suffix>[?+])?\s*:\s*"
    r"(?P<agent>[A-Za-z_][A-Za-z0-9_]*)\s*->\s*"
    r"(?P<output>[A-Za-z_][A-Za-z0-9_]*)\s*"
)


@dataclass(frozen=True)
class RunStageDeclaration:
    name: str
    agent: str
    output_type: str
    cardinality: RunStageCardinality
    raw: str


def parse_run_stage_declaration(value: str) -> RunStageDeclaration | None:
    match = _RUN_STAGE_RE.fullmatch(value)
    if match is None:
        return None
    suffix = match.group("suffix")
    cardinality: RunStageCardinality
    if suffix == "?":
        cardinality = "optional"
    elif suffix == "+":
        cardinality = "many"
    else:
        cardinality = "one"
    return RunStageDeclaration(
        name=match.group("name"),
        agent=match.group("agent"),
        output_type=match.group("output"),
        cardinality=cardinality,
        raw=value,
    )


__all__ = ["RunStageDeclaration", "parse_run_stage_declaration"]
