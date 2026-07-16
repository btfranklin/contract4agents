from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from contract4agents.ast import Authorization, Availability, ContextOrigin, ExecutionBoundary
from contract4agents.expressions._trace_ops import TRACE_OPS
from contract4agents.ir._model import CompositionMode, HistoryMode
from contract4agents.parser import parse_project
from contract4agents.semantic_checks._contracts import (
    ASSESSMENTS,
    AUDIENCES,
    ISOLATION_DIMENSIONS,
    SEVERITIES,
)
from contract4agents.semantics import analyze_project

ROOT = Path(__file__).parents[2]
GRAMMAR_DIR = ROOT / "editors" / "vscode" / "syntaxes"
FIXTURE_DIR = ROOT / "editors" / "vscode" / "test" / "fixtures"


def test_editor_fixtures_are_a_valid_contract_project() -> None:
    result = analyze_project(parse_project(FIXTURE_DIR))

    assert result.ok, [diagnostic.format() for diagnostic in result.diagnostics]


def test_contract_grammar_covers_canonical_closed_vocabulary() -> None:
    patterns = _match_patterns(GRAMMAR_DIR / "contract4agents.tmLanguage.json")
    vocabulary = (
        set(get_args(Availability))
        | set(get_args(Authorization))
        | set(get_args(ExecutionBoundary))
        | set(get_args(ContextOrigin))
        | set(get_args(CompositionMode))
        | set(get_args(HistoryMode))
        | AUDIENCES
        | ASSESSMENTS
        | SEVERITIES
        | {value for values in ISOLATION_DIMENSIONS.values() for value in values}
    )

    assert _missing(vocabulary, patterns) == []


def test_both_grammars_cover_every_registered_trace_operation() -> None:
    for name in ("contract4agents.tmLanguage.json", "contract4agents-eval.tmLanguage.json"):
        assert _missing(set(TRACE_OPS), _match_patterns(GRAMMAR_DIR / name)) == []


def _match_patterns(path: Path) -> str:
    grammar = json.loads(path.read_text())
    matches: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            match = value.get("match")
            if isinstance(match, str):
                matches.append(match)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(grammar)
    return "\n".join(matches)


def _missing(values: set[str], patterns: str) -> list[str]:
    return sorted(value for value in values if value not in patterns)
