"""Lightweight docs consistency checks."""

from __future__ import annotations

import re
from pathlib import Path

from contract4agents.diagnostics import Diagnostic

REQUIRED_DOCS = [
    "AGENTS.md",
    "README.md",
    "LICENSE",
    "VISION.md",
    "docs/index.md",
    "docs/architecture/system-design.md",
    "docs/decisions/accepted-decisions.md",
    "docs/architecture/parser-internals.md",
    "docs/language/contract-language.md",
    "docs/runtime/context-and-datasources.md",
    "docs/compiler/compiler-outputs.md",
    "docs/evaluation/evals-assertions-monitors.md",
    "docs/quality/validation.md",
    "docs/tutorials/using-contract4agents-with-an-agent-app.md",
    "docs/implementation/roadmap.md",
    "docs/decisions/open-questions.md",
    "docs/reference/grammar.md",
    "docs/reference/manifest.md",
    "docs/reference/visualization.md",
    "docs/reference/trace-schema.md",
    "docs/reference/eval-language.md",
    "docs/reference/cli.md",
    "docs/reference/openai-adapter.md",
    "docs/reference/semantic-judge.md",
    "docs/reference/test-fixtures.md",
    "docs/examples/incident-command-walkthrough.md",
    "examples/README.md",
    "examples/incident-command/README.md",
    "examples/multi-lens-research/README.md",
    "examples/market-research-brief/README.md",
]


def check_docs(root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for relative in REQUIRED_DOCS:
        if not (root / relative).exists():
            diagnostics.append(Diagnostic("DOC001", f"Missing required doc `{relative}`"))
    for path in root.rglob("*.md"):
        text = path.read_text()
        for match in re.finditer(r"\]\(([^)]+\.md)(?::\d+)?\)", text):
            target = match.group(1)
            if target.startswith("http"):
                continue
            target_path = (path.parent / target).resolve()
            if not target_path.exists():
                diagnostics.append(Diagnostic("DOC002", f"Broken markdown link `{target}` in {path}"))
    _check_docs_index_paths(root, diagnostics)
    return diagnostics


def _check_docs_index_paths(root: Path, diagnostics: list[Diagnostic]) -> None:
    index_path = root / "docs" / "index.md"
    if not index_path.exists():
        return
    for target in re.findall(r"`([^`]+\.md)`", index_path.read_text()):
        candidates = [(index_path.parent / target).resolve(), (root / target).resolve()]
        if not any(candidate.exists() for candidate in candidates):
            diagnostics.append(Diagnostic("DOC003", f"Broken docs index path `{target}` in {index_path}"))
