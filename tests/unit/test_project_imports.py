from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from contract4agents.runtime._project_imports import (
    ProjectModuleCollisionError,
    import_project_module,
    load_project_python_ref,
)


@pytest.mark.parametrize("module_name", ["calendar", "pydantic"])
def test_project_import_rejects_loaded_standard_and_installed_modules(
    tmp_path: Path,
    module_name: str,
) -> None:
    host_module = importlib.import_module(module_name)
    (tmp_path / f"{module_name}.py").write_text("def implementation():\n    return 'project'\n", encoding="utf-8")
    before = _module_snapshot(module_name)

    with pytest.raises(ProjectModuleCollisionError, match="unique, package-qualified"):
        load_project_python_ref(tmp_path, f"{module_name}:implementation")

    assert _module_snapshot(module_name) == before
    assert sys.modules[module_name] is host_module


def test_project_import_rejects_an_external_parent_package(tmp_path: Path) -> None:
    host_package = importlib.import_module("json")
    package = tmp_path / "json"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "bindings.py").write_text("def implementation():\n    return 'project'\n", encoding="utf-8")
    before = _module_snapshot("json")

    with pytest.raises(ProjectModuleCollisionError) as caught:
        import_project_module(tmp_path, "json.bindings")

    assert caught.value.loaded_name == "json"
    assert _module_snapshot("json") == before
    assert sys.modules["json"] is host_package


def test_project_import_loads_a_unique_dotted_package(tmp_path: Path) -> None:
    package_name = "c4a_project_import_test"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "bindings.py").write_text("def implementation():\n    return 'project'\n", encoding="utf-8")

    try:
        implementation = load_project_python_ref(
            tmp_path,
            f"{package_name}.bindings:implementation",
        )

        assert implementation() == "project"
        assert Path(sys.modules[f"{package_name}.bindings"].__file__).is_relative_to(tmp_path)
    finally:
        _clear_modules(package_name)


def test_project_import_accepts_a_loaded_package_that_contains_the_project(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    package_name = "c4a_repository_package_test"
    package = repository_root / package_name
    project_root = package / "projects" / "sample"
    project_root.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "bindings.py").write_text("VALUE = 'repository'\n", encoding="utf-8")
    sys.path.insert(0, str(repository_root))

    try:
        importlib.import_module(package_name)
        module = import_project_module(project_root, f"{package_name}.bindings")

        assert module.VALUE == "repository"
    finally:
        sys.path.remove(str(repository_root))
        _clear_modules(package_name)


def test_project_import_rejects_reuse_from_a_different_project_root(tmp_path: Path) -> None:
    module_name = "c4a_repeated_import_test"
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / f"{module_name}.py").write_text("VALUE = 'first'\n", encoding="utf-8")
    (second_root / f"{module_name}.py").write_text("VALUE = 'second'\n", encoding="utf-8")

    try:
        first = import_project_module(first_root, module_name)
        before = _module_snapshot(module_name)

        with pytest.raises(ProjectModuleCollisionError):
            import_project_module(second_root, module_name)

        assert first.VALUE == "first"
        assert sys.modules[module_name] is first
        assert _module_snapshot(module_name) == before
    finally:
        _clear_modules(module_name)


def test_failed_project_import_does_not_retain_the_module(tmp_path: Path) -> None:
    module_name = "c4a_broken_import_test"
    (tmp_path / f"{module_name}.py").write_text("raise RuntimeError('broken import')\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="broken import"):
        import_project_module(tmp_path, module_name)

    assert module_name not in sys.modules


def _module_snapshot(prefix: str) -> dict[str, object]:
    return {
        name: module
        for name, module in sys.modules.items()
        if name == prefix or name.startswith(f"{prefix}.")
    }


def _clear_modules(prefix: str) -> None:
    for name in tuple(sys.modules):
        if name == prefix or name.startswith(f"{prefix}."):
            sys.modules.pop(name, None)
