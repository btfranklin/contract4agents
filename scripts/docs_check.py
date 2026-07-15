"""Repository-only documentation consistency checks."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from contract4agents.diagnostics import Diagnostic

REQUIRED_DOCS = [
    "AGENTS.md",
    "README.md",
    "LICENSE",
    "VISION.md",
    "docs/index.md",
]

IGNORED_DOC_DIRS = {
    ".contract",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}


def check_docs(root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    missing_required: set[str] = set()
    for relative in _required_docs(root):
        if not (root / relative).exists():
            missing_required.add(relative)
            diagnostics.append(Diagnostic("DOC001", f"Missing required doc `{relative}`"))
    for path in root.rglob("*.md"):
        if _is_ignored_path(path, root):
            continue
        text = path.read_text()
        for match in re.finditer(r"\]\(([^)]+)\)", text):
            target = _doc_link_target(match.group(1))
            if target is None:
                continue
            if target.startswith("http"):
                continue
            target_path = (path.parent / target).resolve()
            if not target_path.exists():
                diagnostics.append(Diagnostic("DOC002", f"Broken markdown link `{target}` in {path}"))
    _check_docs_index_paths(root, diagnostics, missing_required)
    return diagnostics


def _required_docs(root: Path) -> list[str]:
    required = set(REQUIRED_DOCS)
    index_path = root / "docs" / "index.md"
    if index_path.exists():
        required.update(
            _root_relative_index_target(target, root, index_path) for target in _docs_index_paths(index_path, root)
        )
    return sorted(required)


def _check_docs_index_paths(root: Path, diagnostics: list[Diagnostic], missing_required: set[str]) -> None:
    index_path = root / "docs" / "index.md"
    if not index_path.exists():
        return
    for target in _docs_index_paths(index_path, root):
        if _root_relative_index_target(target, root, index_path) in missing_required:
            continue
        candidates = [(index_path.parent / target).resolve(), (root / target).resolve()]
        if not any(candidate.exists() for candidate in candidates):
            diagnostics.append(Diagnostic("DOC003", f"Broken docs index path `{target}` in {index_path}"))


def _docs_index_paths(index_path: Path, root: Path) -> set[str]:
    text = index_path.read_text()
    paths = set(re.findall(r"`([^`]+\.md(?:#[^`]+)?)`", text))
    paths.update(
        target
        for target in (_doc_link_target(match.group(1)) for match in re.finditer(r"\]\(([^)]+)\)", text))
        if target
    )
    return {_strip_doc_anchor(path) for path in paths if _is_repo_doc_path(path, root)}


def _root_relative_index_target(target: str, root: Path, index_path: Path) -> str:
    root_candidate = (root / target).resolve()
    if root_candidate.exists() or target in REQUIRED_DOCS:
        try:
            return str(root_candidate.relative_to(root.resolve()))
        except ValueError:
            pass
    resolved = (index_path.parent / target).resolve()
    try:
        return str(resolved.relative_to(root.resolve()))
    except ValueError:
        return target


def _doc_link_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<"):
        close = target.find(">")
        if close == -1:
            return None
        target = target[1:close]
    else:
        target = target.split(" ", 1)[0]
    target = _strip_doc_anchor(target)
    target = re.sub(r":\d+$", "", target)
    if not target.endswith(".md"):
        return None
    return target


def _strip_doc_anchor(path: str) -> str:
    return path.split("#", 1)[0]


def _is_ignored_path(path: Path, root: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        return False
    return any(part in IGNORED_DOC_DIRS for part in relative_parts)


def _is_repo_doc_path(path: str, root: Path) -> bool:
    if path.startswith(("http://", "https://", "mailto:", "#")):
        return False
    if path.startswith("../"):
        return (root / "docs" / path).resolve().is_relative_to(root.resolve())
    return True


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    diagnostics = check_docs(root)
    for diagnostic in diagnostics:
        print(diagnostic.format(), file=sys.stderr)
    if diagnostics:
        raise SystemExit(1)
    print("Docs check passed")


if __name__ == "__main__":
    main()
