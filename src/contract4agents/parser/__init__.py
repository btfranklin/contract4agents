"""Public parser API for `.contract` and `.eval` files."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn, cast

from lark import UnexpectedInput
from lark.exceptions import VisitError

from contract4agents.ast import ContractModule, ContractProject, SourceSpan
from contract4agents.diagnostics import ContractError, Diagnostic
from contract4agents.parser._grammar import MODULE_PARSER
from contract4agents.parser._transformer import _ModuleTransformer


def parse_project(root: Path | str) -> ContractProject:
    root_path = Path(root)
    modules = [parse_file(path) for path in _source_paths(root_path, "*.contract")]
    modules.extend(parse_file(path) for path in _source_paths(root_path, "*.eval"))
    return ContractProject(root=root_path, modules=modules)


def _source_paths(root_path: Path, pattern: str) -> list[Path]:
    return sorted(
        path
        for path in root_path.rglob(pattern)
        if path.is_file() and ".contract" not in {part for part in path.parts}
    )


def parse_file(path: Path | str) -> ContractModule:
    source_path = Path(path)
    try:
        source = source_path.read_text()
        if source and not source.endswith("\n"):
            source += "\n"
        tree = MODULE_PARSER.parse(source)
        module = _ModuleTransformer(source_path).transform(tree)
    except UnexpectedInput as exc:
        _raise("PARSE001", "Invalid syntax", source_path, exc.line or 1, str(exc), exc.column or 1)
    except VisitError as exc:
        if isinstance(exc.orig_exc, ContractError):
            raise exc.orig_exc from exc
        raise
    return cast(ContractModule, module)


def _raise(
    code: str,
    message: str,
    path: Path,
    line: int,
    hint: str | None = None,
    column: int = 1,
) -> NoReturn:
    raise ContractError([Diagnostic(code, message, span=SourceSpan(path, line, column), hint=hint)])
