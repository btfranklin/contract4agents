"""Safe imports for Python references owned by one contract project."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any


class ProjectModuleCollisionError(ImportError):
    """A project locator conflicts with a module that the host already owns."""

    def __init__(self, module_name: str, loaded_name: str, origin: str) -> None:
        self.module_name = module_name
        self.loaded_name = loaded_name
        self.origin = origin
        super().__init__(
            f"Project module `{module_name}` conflicts with loaded host module `{loaded_name}` "
            f"from `{origin}`. Use a unique, package-qualified application module name."
        )


def import_project_module(project_root: Path, module_name: str) -> ModuleType:
    """Import one non-colliding module from a project root."""

    root = project_root.resolve()
    _reject_loaded_host_owners(root, module_name)
    with _project_import_path(root):
        return importlib.import_module(module_name)


def load_project_python_ref(project_root: Path, reference: str) -> Any:
    """Load one `module:attribute` reference from a project root."""

    module_name, separator, attribute = reference.partition(":")
    if not module_name or not separator or not attribute:
        raise ValueError(f"Invalid python reference: {reference}")
    module = import_project_module(project_root, module_name)
    return getattr(module, attribute)


def _reject_loaded_host_owners(project_root: Path, module_name: str) -> None:
    parts = module_name.split(".")
    ownership_roots = [project_root]
    for length in range(1, len(parts) + 1):
        loaded_name = ".".join(parts[:length])
        loaded = sys.modules.get(loaded_name)
        if loaded is None or _module_is_inside_roots(loaded, ownership_roots):
            continue
        package_roots = _package_roots_containing_project(loaded, project_root)
        if package_roots:
            ownership_roots.extend(package_roots)
            continue
        raise ProjectModuleCollisionError(
            module_name,
            loaded_name,
            _module_origin(loaded),
        )


def _module_is_inside_roots(module: ModuleType, ownership_roots: list[Path]) -> bool:
    loaded_file = getattr(module, "__file__", None)
    if isinstance(loaded_file, str):
        return any(Path(loaded_file).resolve().is_relative_to(root) for root in ownership_roots)
    loaded_paths = getattr(module, "__path__", None)
    if loaded_paths is None:
        return False
    paths = tuple(Path(path).resolve() for path in loaded_paths)
    return bool(paths) and all(any(path.is_relative_to(root) for root in ownership_roots) for path in paths)


def _package_roots_containing_project(module: ModuleType, project_root: Path) -> tuple[Path, ...]:
    loaded_paths = getattr(module, "__path__", None)
    if loaded_paths is None:
        return ()
    paths = tuple(Path(path).resolve() for path in loaded_paths)
    if not paths or not all(project_root.is_relative_to(path) for path in paths):
        return ()
    return paths


def _module_origin(module: ModuleType) -> str:
    loaded_file = getattr(module, "__file__", None)
    if isinstance(loaded_file, str):
        return str(Path(loaded_file).resolve())
    loaded_paths = getattr(module, "__path__", None)
    if loaded_paths is not None:
        paths = tuple(str(Path(path).resolve()) for path in loaded_paths)
        if paths:
            return ", ".join(paths)
    return "built-in or unknown origin"


@contextmanager
def _project_import_path(project_root: Path) -> Iterator[None]:
    path = str(project_root)
    inserted = path not in sys.path
    if inserted:
        sys.path.insert(0, path)
    importlib.invalidate_caches()
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(path)
            except ValueError:
                pass


__all__ = [
    "ProjectModuleCollisionError",
    "import_project_module",
    "load_project_python_ref",
]
