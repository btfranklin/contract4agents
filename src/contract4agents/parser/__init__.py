"""Public parser API for `.contract` and `.eval` files."""

from __future__ import annotations

from pathlib import Path

from contract4agents.ast import ContractModule, ContractProject
from contract4agents.parser._parse import parse_source_syntax


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
    return parse_source(source_path, source_path.read_text())


def parse_source(path: Path | str, source: str) -> ContractModule:
    """Parse unsaved source text using the canonical module parser."""

    return parse_source_syntax(Path(path), source).module


__all__ = ["parse_file", "parse_project", "parse_source"]
