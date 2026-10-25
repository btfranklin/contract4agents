"""Check the license and inline document paths in the documentation index."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from contract4agents.diagnostics import Diagnostic


def check_docs(root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if not (root / "LICENSE").is_file():
        diagnostics.append(Diagnostic("DOC001", "Missing required doc `LICENSE`"))
    index_path = root / "docs" / "index.md"
    if not index_path.exists():
        return diagnostics  # Perfect Doc checks required Markdown files.
    targets = set(re.findall(r"`([^`]+\.md(?:#[^`]+)?)`", index_path.read_text()))
    for target in sorted(targets):
        path = target.split("#", 1)[0]
        candidates = ((root / path).resolve(), (index_path.parent / path).resolve())
        if path.startswith(("http://", "https://", "mailto:")):
            continue
        if any(candidate.is_relative_to(root.resolve()) and candidate.exists() for candidate in candidates):
            continue
        diagnostics.append(Diagnostic("DOC003", f"Broken docs index path `{target}` in {index_path}"))
    return diagnostics


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
