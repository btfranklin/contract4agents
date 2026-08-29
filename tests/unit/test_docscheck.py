from __future__ import annotations

import tomllib
from pathlib import Path

from scripts.docs_check import REQUIRED_DOCS, check_docs

ROOT = Path(__file__).resolve().parents[2]


def test_pdm_docs_check_is_part_of_validate() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    scripts = pyproject["tool"]["pdm"]["scripts"]

    assert scripts["docs-check"] == "python scripts/docs_check.py"
    assert scripts["test"] == "pytest"
    assert scripts["smoke:cli"] == "python scripts/smoke_cli.py"
    assert "validate:python" in scripts["validate"]["composite"]
    assert "docs-check" in scripts["validate:python"]["composite"]


def test_release_workflows_gate_the_exact_tag_before_draft_and_publication() -> None:
    draft_workflow = (ROOT / ".github/workflows/create-draft-release.yml").read_text()
    publish_workflow = (ROOT / ".github/workflows/python-publish.yml").read_text()

    draft_gate = (
        "ref: ${{ github.ref }}",
        "pdm install --group dev --group openai --group strands --group google-adk",
        "pdm run validate:python",
        "pdm run smoke:cli",
        "pdm build",
        "pdm run package-check",
        "npm test",
        "npm run package",
        "uses: btfranklin/release-notes-scribe@v0",
    )
    publish_gate = (
        "ref: ${{ github.event.release.tag_name }}",
        "pdm install --group dev --group openai --group strands --group google-adk",
        "pdm run validate:python",
        "pdm run smoke:cli",
        "pdm build",
        "pdm run package-check",
        "uses: pypa/gh-action-pypi-publish@release/v1",
    )

    assert _positions(draft_workflow, draft_gate) == sorted(_positions(draft_workflow, draft_gate))
    assert _positions(publish_workflow, publish_gate) == sorted(
        _positions(publish_workflow, publish_gate)
    )


def test_docs_check_reports_missing_doc(tmp_path: Path) -> None:
    diagnostics = check_docs(tmp_path)

    assert diagnostics
    assert diagnostics[0].code == "DOC001"


def _positions(source: str, required: tuple[str, ...]) -> list[int]:
    positions = [source.find(item) for item in required]
    assert all(position >= 0 for position in positions)
    return positions


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
    for relative in ["docs/reference/cli.md", "docs/reference/trace-schema.md"]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n")
    (tmp_path / "docs" / "index.md").write_text(
        "- `reference/cli.md#assess-root-trace-trace-jsonl`\n"
        "- [Trace](<reference/trace-schema.md:12>)\n"
    )
    (tmp_path / "README.md").write_text("[CLI](<docs/reference/cli.md#assess-root-trace-trace-jsonl>)\n")

    assert check_docs(tmp_path) == []


def test_docs_check_uses_docs_index_as_required_map(tmp_path: Path) -> None:
    for relative in REQUIRED_DOCS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n")
    (tmp_path / "docs" / "index.md").write_text("- `quality/validation.md`\n")

    diagnostics = check_docs(tmp_path)

    assert [(diagnostic.code, diagnostic.message) for diagnostic in diagnostics] == [
        ("DOC001", "Missing required doc `docs/quality/validation.md`")
    ]


def test_docs_check_ignores_dependency_and_build_directories(tmp_path: Path) -> None:
    for relative in REQUIRED_DOCS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n")
    for directory in ["node_modules", "dist", ".contract"]:
        path = tmp_path / directory / "README.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[broken](missing.md)\n")

    assert check_docs(tmp_path) == []
