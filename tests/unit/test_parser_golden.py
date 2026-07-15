from __future__ import annotations

import json
from pathlib import Path

from contract4agents.parser import parse_project
from contract4agents.semantics import analyze_project

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "contract_projects" / "parser-golden" / "surface-lab"
GOLDEN = ROOT / "tests" / "golden" / "ast" / "surface-lab.json"


def test_parser_surface_matches_golden() -> None:
    project = parse_project(FIXTURE)

    assert _snapshot(project) == json.loads(GOLDEN.read_text())


def test_parser_golden_is_semantically_valid() -> None:
    result = analyze_project(parse_project(FIXTURE))

    assert result.ok, [item.format() for item in result.diagnostics]


def _snapshot(project: object) -> dict[str, list[str]]:
    agents = project.agents  # type: ignore[attr-defined]
    return {
        "agents": sorted(agents),
        "compositions": sorted(project.compositions),  # type: ignore[attr-defined]
        "contexts": sorted(
            f"{agent.name}.{item.name}:{item.origin}:{item.source or ''}"
            for agent in agents.values()
            for item in agent.context
        ),
        "controls": sorted(f"{item.agent}.{item.name}" for item in project.controls),  # type: ignore[attr-defined]
        "datasources": sorted(
            f"{item.name}({','.join(field.type_name for field in item.parameters)})->{item.return_type}"
            for item in project.datasources.values()  # type: ignore[attr-defined]
        ),
        "evals": sorted(f"{item.agent}.{item.name}" for item in project.evals),  # type: ignore[attr-defined]
        "external_contexts": sorted(
            f"{item.name}->{item.type_name}" for item in project.external_contexts.values()  # type: ignore[attr-defined]
        ),
        "grants": sorted(
            f"{agent.name}:{item.capability}:{item.availability}:{item.authorization}:{item.execution}"
            for agent in agents.values()
            for item in agent.grants
        ),
        "isolations": sorted(project.isolations),  # type: ignore[attr-defined]
        "operational_controls": sorted(
            f"{item.agent}.{item.name}" for item in project.operational_controls  # type: ignore[attr-defined]
        ),
        "qualities": sorted(f"{item.agent}.{item.name}" for item in project.qualities),  # type: ignore[attr-defined]
        "run_specs": sorted(project.run_specs),  # type: ignore[attr-defined]
        "tools": sorted(
            f"{item.name}({','.join(field.type_name for field in item.parameters)})->{item.return_type}"
            for item in project.tools.values()  # type: ignore[attr-defined]
        ),
        "types": sorted(project.types),  # type: ignore[attr-defined]
    }
