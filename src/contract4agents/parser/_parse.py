"""Canonical parsing of in-memory Contract4Agents source."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from lark import Token, Tree, UnexpectedInput
from lark.exceptions import VisitError

from contract4agents.ast import ContractModule, SourceSpan
from contract4agents.diagnostics import ContractError, Diagnostic
from contract4agents.parser._grammar import MODULE_PARSER
from contract4agents.parser._transformer import _ModuleTransformer


@dataclass(frozen=True)
class ParsedSource:
    module: ContractModule
    tree: Tree[Token]


def parse_source_syntax(path: Path, source: str) -> ParsedSource:
    """Parse source once and retain both the canonical AST and positioned syntax tree."""

    normalized = source if not source or source.endswith("\n") else source + "\n"
    try:
        tree = MODULE_PARSER.parse(normalized)
        module = _ModuleTransformer(path).transform(tree)
    except UnexpectedInput as exc:
        _raise("PARSE001", "Invalid syntax", path, exc.line or 1, str(exc), exc.column or 1)
    except VisitError as exc:
        if isinstance(exc.orig_exc, ContractError):
            raise exc.orig_exc from exc
        raise
    _validate_file_kind(path, module)
    return ParsedSource(module, tree)


_DECLARATION_LABELS = {
    "types": "type",
    "enums": "enum",
    "tools": "tool",
    "datasources": "datasource",
    "external_contexts": "external_context",
    "isolations": "isolation",
    "compositions": "composition",
    "controls": "control",
    "qualities": "quality",
    "operational_controls": "operational_control",
    "agents": "agent",
    "run_specs": "run_spec",
    "evals": "eval",
}
_FILE_KINDS: dict[str, tuple[str, ...]] = {
    ".agent.contract": ("agents",),
    ".types.contract": ("types", "enums"),
    ".capabilities.contract": ("tools", "datasources", "external_contexts"),
    ".composition.contract": ("compositions",),
    ".assurance.contract": ("controls", "qualities", "operational_controls"),
    ".run.contract": ("run_specs",),
    ".eval": ("evals",),
}


def _validate_file_kind(path: Path, module: ContractModule) -> None:
    suffix = next((item for item in _FILE_KINDS if path.name.endswith(item)), None)
    if suffix is None:
        return

    allowed = _FILE_KINDS[suffix]
    declarations = [(name, item) for name in _DECLARATION_LABELS for item in getattr(module, name)]
    accepted = [item for name, item in declarations if name in allowed]
    rejected = [(name, item) for name, item in declarations if name not in allowed]
    if rejected or (allowed != ("agents",) and not accepted) or (
        allowed == ("agents",) and len(accepted) != 1
    ):
        actual = ", ".join(_DECLARATION_LABELS[name] for name, _ in declarations) or "no declarations"
        expected = " or ".join(_DECLARATION_LABELS[name] for name in allowed)
        requirement = (
            "exactly one agent and no other top-level declarations"
            if suffix == ".agent.contract"
            else f"one or more {expected} declarations and no other top-level declarations"
        )
        item = rejected[0][1] if rejected else (accepted[1] if len(accepted) > 1 else None)
        span = getattr(item, "span", None) or SourceSpan(path, 1)
        raise ContractError(
            [
                Diagnostic(
                    "PARSE003",
                    f"File `{path.name}` requires {requirement}; found {actual}",
                    span=span,
                )
            ]
        )


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
