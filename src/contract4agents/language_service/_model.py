"""Editor-facing source and symbol models.

These models deliberately sit beside the compiler AST.  The AST represents
language meaning; this module records where that meaning appears in source so
interactive tools can answer cursor-position queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from contract4agents.ast import ContractModule
from contract4agents.diagnostics import Diagnostic

OccurrenceRole = Literal["definition", "reference", "property", "value", "keyword"]
SemanticTokenKind = Literal["type", "class", "function", "interface", "property", "parameter", "enumMember"]
SymbolKind = Literal[
    "agent",
    "composition",
    "control",
    "datasource",
    "enum",
    "eval",
    "external_context",
    "field",
    "isolation",
    "operational_control",
    "quality",
    "run_spec",
    "stage",
    "tool",
    "type",
]


@dataclass(frozen=True)
class SymbolId:
    kind: SymbolKind
    name: str
    owner: str | None = None


@dataclass(frozen=True, order=True)
class SourcePosition:
    """Zero-based source-code-point position, independent of LSP client units."""

    line: int
    character: int


@dataclass(frozen=True)
class SourceRange:
    start: SourcePosition
    end: SourcePosition

    def contains(self, position: SourcePosition, *, include_end: bool = False) -> bool:
        if include_end:
            return self.start <= position <= self.end
        return self.start <= position < self.end

    @property
    def specificity(self) -> tuple[int, int]:
        return (self.end.line - self.start.line, self.end.character - self.start.character)


@dataclass(frozen=True)
class SourceOccurrence:
    text: str
    range: SourceRange
    role: OccurrenceRole
    symbol: SymbolId | None = None
    context: str | None = None
    container: str | None = None


@dataclass(frozen=True)
class SourceSemanticToken:
    range: SourceRange
    kind: SemanticTokenKind
    declaration: bool = False


@dataclass(frozen=True)
class SourceDeclaration:
    name: str
    kind: SymbolKind
    range: SourceRange
    selection_range: SourceRange
    detail: str = ""
    children: tuple[SourceDeclaration, ...] = ()


@dataclass
class SourceDocument:
    path: Path
    source: str
    module: ContractModule | None
    occurrences: tuple[SourceOccurrence, ...] = ()
    declarations: tuple[SourceDeclaration, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def occurrence_at(self, position: SourcePosition) -> SourceOccurrence | None:
        candidates = [item for item in self.occurrences if item.range.contains(position, include_end=True)]
        if not candidates:
            return None
        role_priority = {"reference": 0, "definition": 1, "value": 2, "property": 3, "keyword": 4}
        return min(candidates, key=lambda item: (item.range.specificity, role_priority[item.role]))

@dataclass(frozen=True)
class CompletionSuggestion:
    label: str
    detail: str
    documentation: str
    kind: Literal["keyword", "property", "value", "type", "function", "class"] = "value"
    insert_text: str | None = None


@dataclass(frozen=True)
class SourceEdit:
    path: Path
    range: SourceRange
    new_text: str


@dataclass(frozen=True)
class QuickFix:
    title: str
    edits: tuple[SourceEdit, ...]
    preferred: bool = False


@dataclass(frozen=True)
class SourceInlayHint:
    position: SourcePosition
    label: str
    tooltip: str


__all__ = [
    "CompletionSuggestion",
    "OccurrenceRole",
    "QuickFix",
    "SemanticTokenKind",
    "SourceDeclaration",
    "SourceDocument",
    "SourceEdit",
    "SourceInlayHint",
    "SourceOccurrence",
    "SourcePosition",
    "SourceRange",
    "SourceSemanticToken",
    "SymbolId",
    "SymbolKind",
]
