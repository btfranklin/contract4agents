"""Import helpers for capability registry Python references."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contract4agents.diagnostics import Diagnostic
from contract4agents.runtime import load_python_ref


@dataclass(frozen=True)
class LoadedRef:
    value: Any | None
    diagnostics: list[Diagnostic]


def load_registry_ref(root: Path, registry_entry: str, reference: str) -> LoadedRef:
    try:
        with project_import_path(root):
            return LoadedRef(load_python_ref(reference), [])
    except Exception as exc:
        return LoadedRef(
            None,
            [
                Diagnostic(
                    "CAP020",
                    f"Could not import `{reference}` for capability registry entry `{registry_entry}`",
                    hint=str(exc),
                )
            ],
        )


@contextmanager
def project_import_path(root: Path) -> Iterator[None]:
    paths = [str(path) for path in _registry_import_paths(root)]
    inserted: list[str] = []
    for path in reversed(paths):
        if path in sys.path:
            continue
        sys.path.insert(0, path)
        inserted.append(path)
    try:
        yield
    finally:
        for path in inserted:
            try:
                sys.path.remove(path)
            except ValueError:
                pass


def _registry_import_paths(root: Path) -> list[Path]:
    project_root = root.resolve()
    paths = [project_root]
    for parent in (project_root, *project_root.parents):
        if (parent / "pyproject.toml").exists():
            pyproject_root = parent.resolve()
            if pyproject_root not in paths:
                paths.append(pyproject_root)
            break
    return paths


__all__ = ["LoadedRef", "load_registry_ref", "project_import_path"]
