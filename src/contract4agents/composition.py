"""Parsing helpers for agent composition declarations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, cast

CompositionMode = Literal["agent_as_tool", "handoff", "isolated_subagent"]

_COMPOSITION_RE = re.compile(r"\s*(agent_as_tool|handoff|isolated_subagent)\(([A-Za-z_][A-Za-z0-9_]*)\)\s*")


@dataclass(frozen=True)
class CompositionDeclaration:
    mode: CompositionMode
    agent: str


def parse_composition_declaration(raw: str) -> CompositionDeclaration | None:
    match = _COMPOSITION_RE.fullmatch(raw)
    if not match:
        return None
    mode, agent = match.groups()
    return CompositionDeclaration(cast(CompositionMode, mode), agent)
