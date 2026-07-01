from __future__ import annotations

import tomllib
from pathlib import Path

from contract4agents.docscheck import REQUIRED_DOCS, check_docs

ROOT = Path(__file__).resolve().parents[2]


def test_pdm_docs_check_is_part_of_validate() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    scripts = pyproject["tool"]["pdm"]["scripts"]

    assert scripts["docs-check"] == "python -m contract4agents docs-check"
    assert "docs-check" in scripts["validate"]["composite"]


def test_docs_check_reports_missing_doc(tmp_path: Path) -> None:
    diagnostics = check_docs(tmp_path)

    assert diagnostics
    assert diagnostics[0].code == "DOC001"


def test_docs_check_validates_docs_index_backtick_paths(tmp_path: Path) -> None:
    for relative in REQUIRED_DOCS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n")
    (tmp_path / "docs" / "index.md").write_text("- `missing/doc.md`\n")

    diagnostics = check_docs(tmp_path)

    assert [(diagnostic.code, diagnostic.message) for diagnostic in diagnostics] == [
        ("DOC001", "Missing required doc `docs/missing/doc.md`")
    ]


def test_docs_check_supports_anchor_line_and_angle_markdown_links(tmp_path: Path) -> None:
    for relative in REQUIRED_DOCS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n")
    (tmp_path / "docs" / "index.md").write_text(
        "- `reference/cli.md#monitor-root-trace-trace-jsonl`\n"
        "- [Trace](<reference/trace-schema.md:12>)\n"
    )
    (tmp_path / "README.md").write_text("[CLI](<docs/reference/cli.md#monitor-root-trace-trace-jsonl>)\n")

    assert check_docs(tmp_path) == []
