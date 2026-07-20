"""Canonical parsing of in-memory Contract4Agents source."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

from lark import Tree, UnexpectedInput
from lark.exceptions import VisitError

from contract4agents.ast import ContractModule, SourceSpan
from contract4agents.diagnostics import ContractError, Diagnostic
from contract4agents.parser._grammar import MODULE_PARSER
from contract4agents.parser._transformer import _ModuleTransformer


@dataclass(frozen=True)
class ParsedSource:
    module: ContractModule
    tree: Tree[Any]


def parse_source_syntax(path: Path, source: str) -> ParsedSource:
    """Parse source once and retain both the canonical AST and positioned syntax tree."""

    normalized = source if not source or source.endswith("\n") else source + "\n"
    try:
        tree = MODULE_PARSER.parse(normalized)
        module = cast(ContractModule, _ModuleTransformer(path).transform(tree))
    except UnexpectedInput as exc:
        _raise("PARSE001", "Invalid syntax", path, exc.line or 1, str(exc), exc.column or 1)
    except VisitError as exc:
        if isinstance(exc.orig_exc, ContractError):
            raise exc.orig_exc from exc
        raise
    return ParsedSource(module, tree)


def _raise(
    code: str,
    message: str,
    path: Path,
    line: int,
    hint: str | None = None,
    column: int = 1,
) -> NoReturn:
    raise ContractError([Diagnostic(code, message, span=SourceSpan(path, line, column), hint=hint)])


__all__ = ["ParsedSource", "parse_source_syntax"]
