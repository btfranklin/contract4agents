from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from contract4agents.capability_registry import (
    check_capability_drift,
    load_capability_registry,
)
from contract4agents.cli import main
from contract4agents.compiler import build_artifacts
from contract4agents.parser import parse_project

ROOT = Path(__file__).resolve().parents[2]
HOST_DRIFT = ROOT / "tests" / "fixtures" / "contract_projects" / "host-drift"


def test_capability_registry_strict_happy_path() -> None:
    assert _strict_diagnostics(HOST_DRIFT) == []


def test_capability_registry_reports_malformed_registry(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    (project / "contract4agents.registry.json").write_text('{"version": 2, "tools": []}\n')

    load = load_capability_registry(project, required=True)

    assert _codes(load.diagnostics) == ["CAP001"]
    assert "section `tools`" in load.diagnostics[0].message


def test_capability_registry_rejects_v1_registry_shape(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(project, lambda data: data.update({"version": 1}))

    load = load_capability_registry(project, required=True)

    assert _codes(load.diagnostics) == ["CAP001"]
    assert "version: 2" in load.diagnostics[0].message


def test_capability_registry_allows_non_strict_project_without_registry(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.contract").write_text(
        """
type Input:
    value: str

type Output:
    value: str

agent LocalAgent(input: Input) -> Output:
    goal = "Return output."
""".strip()
        + "\n"
    )

    load = load_capability_registry(project)

    assert load.registry is None
    assert load.diagnostics == []


def test_capability_registry_reports_missing_tool_source(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(project, lambda data: data["tools"].pop("drift.lookup"))

    diagnostics = _strict_diagnostics(project)

    assert _codes(diagnostics) == ["CAP010"]
    assert "drift.lookup" in diagnostics[0].message


def test_capability_registry_reports_misspelled_callable(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(
        project,
        lambda data: data["tools"]["drift.lookup"].update(
            {"python": "tests.fixtures.host_drift_app:missing_lookup"}
        ),
    )

    diagnostics = _strict_diagnostics(project)

    assert _codes(diagnostics) == ["CAP020"]
    assert "missing_lookup" in diagnostics[0].message


def test_capability_registry_reports_non_callable_tool_ref(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(
        project,
        lambda data: data["tools"]["drift.lookup"].update(
            {"python": "tests.fixtures.host_drift_app:NOT_CALLABLE"}
        ),
    )

    diagnostics = _strict_diagnostics(project)

    assert _codes(diagnostics) == ["CAP021"]
    assert "not callable" in diagnostics[0].message


def test_capability_registry_reports_permission_mismatch(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(
        project,
        lambda data: data["tools"]["drift.lookup"]["permissions"].update({"HostDriftAgent": "denied"}),
    )

    diagnostics = _strict_diagnostics(project)

    assert _codes(diagnostics) == ["CAP030"]
    assert "permission mismatch" in diagnostics[0].message


def test_capability_registry_allows_external_host_provided_tool(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(
        project,
        lambda data: data["tools"].update(
            {"drift.lookup": {"external": True, "permissions": {"HostDriftAgent": "preapproved"}}}
        ),
    )

    assert _strict_diagnostics(project) == []


def test_capability_registry_reports_agent_name_drift(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(project, lambda data: data["agents"]["HostDriftAgent"].update({"name": "RenamedAgent"}))

    diagnostics = _strict_diagnostics(project)

    assert _codes(diagnostics) == ["CAP040"]
    assert "RenamedAgent" in diagnostics[0].message


def test_capability_registry_reports_pydantic_output_type_mismatch(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(
        project,
        lambda data: data["output_types"]["DriftResult"].update(
            {"python": "tests.fixtures.host_drift_app:DriftWrongResultModel"}
        ),
    )

    diagnostics = _strict_diagnostics(project)

    assert _codes(diagnostics) == ["CAP050"]
    assert "DriftResult" in diagnostics[0].message


def test_capability_registry_reports_hosted_tool_drift(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(
        project,
        lambda data: data["hosted_tools"]["openai.web_search"]["config"].update({"context_size": "large"}),
    )

    diagnostics = _strict_diagnostics(project)

    assert _codes(diagnostics) == ["CAP060"]
    assert "openai.web_search" in diagnostics[0].message


def test_capability_registry_reports_hosted_tool_permission_mismatch(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(
        project,
        lambda data: data["hosted_tools"]["openai.web_search"]["permissions"].update(
            {"HostDriftAgent": "denied"}
        ),
    )

    diagnostics = _strict_diagnostics(project)

    assert _codes(diagnostics) == ["CAP030"]
    assert "HostDriftAgent" in diagnostics[0].message


def test_capability_registry_reports_missing_per_agent_tool_permission(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(
        project,
        lambda data: data["tools"]["drift.lookup"].update({"permissions": {"OtherAgent": "preapproved"}}),
    )

    diagnostics = _strict_diagnostics(project)

    assert _codes(diagnostics) == ["CAP030", "CAP090"]
    assert "HostDriftAgent" in diagnostics[0].message
    assert "OtherAgent" in diagnostics[1].message


def test_capability_registry_reports_stale_tool_hosted_tool_and_agent_entries(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(
        project,
        lambda data: (
            data["tools"].update({"stale.tool": {"external": True, "permissions": {"HostDriftAgent": "available"}}}),
            data["hosted_tools"].update(
                {
                    "openai.stale": {
                        "provider": "openai",
                        "tool": "stale",
                        "config": {},
                        "permissions": {"HostDriftAgent": "available"},
                    }
                }
            ),
            data["agents"].update({"StaleAgent": {"name": "StaleAgent"}}),
        ),
    )

    diagnostics = _strict_diagnostics(project)

    assert _codes(diagnostics) == ["CAP090", "CAP090", "CAP090"]
    assert {diagnostic.message for diagnostic in diagnostics} == {
        "Tool registry entry `stale.tool` does not match any contract declaration",
        "Hosted tool registry entry `openai.stale` does not match any contract declaration",
        "Agent registry entry `StaleAgent` does not match any contract agent",
    }


def test_capability_registry_reports_prompt_asset_drift(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(
        project,
        lambda data: data["prompts"]["HostDriftAgent"].update({"path": "prompts/missing.md"}),
    )

    diagnostics = _strict_diagnostics(project)

    assert _codes(diagnostics) == ["CAP070"]
    assert "missing.md" in diagnostics[0].message


def test_capability_registry_reports_host_context_marker_drift(tmp_path: Path) -> None:
    project = _copy_host_drift(tmp_path)
    _mutate_registry(project, lambda data: data["host_context"].update({"HostDriftAgent": []}))

    diagnostics = _strict_diagnostics(project)

    assert _codes(diagnostics) == ["CAP080"]
    assert "HostSummary" in diagnostics[0].message


def test_cli_strict_drift_and_registry_override(tmp_path: Path) -> None:
    runner = CliRunner()
    project = _copy_host_drift(tmp_path)
    default_registry = project / "contract4agents.registry.json"
    override_registry = project / "registry.override.json"
    override_registry.write_text(default_registry.read_text())
    default_registry.unlink()

    missing = runner.invoke(main, ["check", str(project), "--strict-drift"])
    override = runner.invoke(
        main,
        ["check", str(project), "--strict-drift", "--registry", str(override_registry)],
    )
    non_strict = runner.invoke(main, ["check", str(project)])

    assert missing.exit_code != 0
    assert "CAP002" in missing.output
    assert override.exit_code == 0
    assert "passed" in override.output
    assert non_strict.exit_code == 0


def test_capability_registry_imports_work_after_chdir_away_from_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sys.modules.pop("tests.fixtures.host_drift_app", None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [item for item in sys.path if item not in {"", str(ROOT)}])

    diagnostics = _strict_diagnostics(HOST_DRIFT)

    assert diagnostics == []


def _strict_diagnostics(project: Path) -> list:
    contract_project = parse_project(project)
    artifacts = build_artifacts(contract_project)
    load = load_capability_registry(project, required=True)
    if load.diagnostics or load.registry is None:
        return load.diagnostics
    return check_capability_drift(contract_project, artifacts, load.registry)


def _copy_host_drift(tmp_path: Path) -> Path:
    project = tmp_path / "host-drift"
    shutil.copytree(HOST_DRIFT, project)
    return project


def _mutate_registry(project: Path, mutator) -> None:
    path = project / "contract4agents.registry.json"
    data = json.loads(path.read_text())
    mutator(data)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _codes(diagnostics: list) -> list[str]:
    return [diagnostic.code for diagnostic in diagnostics]
