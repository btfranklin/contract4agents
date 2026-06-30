"""Structured diagnostics used by all Contract4Agents phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from contract4agents.ast import SourceSpan

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: Severity = "error"
    span: SourceSpan | None = None
    hint: str | None = None

    def format(self) -> str:
        location = f"{self.span.display()} " if self.span else ""
        hint = f"\n  hint: {self.hint}" if self.hint else ""
        return f"{location}{self.severity.upper()} {self.code}: {self.message}{hint}"


class ContractError(Exception):
    """Raised when a Contract4Agents phase cannot continue."""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics = diagnostics
        message = "\n".join(diagnostic.format() for diagnostic in diagnostics)
        super().__init__(message)


def raise_if_errors(diagnostics: list[Diagnostic]) -> None:
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    if errors:
        raise ContractError(errors)
