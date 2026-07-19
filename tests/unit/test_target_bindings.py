from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract4agents.target_bindings import (
    DEFAULT_TARGET_BINDINGS_FILENAME,
    TARGET_BINDINGS_SCHEMA_VERSION,
    canonical_target_bindings_json,
    load_target_bindings,
    target_bindings_dict,
)

VALID_BINDINGS = """
schema_version = "2"

[targets.openai]
adapter = "openai"

[targets.openai.tools."incident.fetch_logs"]
python = "incident_app.tools:fetch_logs"

[targets.openai.datasources."incident.timeline"]
python = "incident_app.datasources:timeline"

[targets.openai.external_context.incident_record]
python = "incident_app.context:current_incident"

[targets.openai.environments.in_process]
provider = "contract4agents.runtime:InProcessEnvironment"

[targets.openai.profiles.test]
default_model = "test-model"

[targets.openai.profiles.test.options]
temperature = 0.0
tags = ["offline", "deterministic"]

[targets.openai.profiles.test.agents.LogInvestigator]
model = "gpt-5.6-luna"

[targets.openai.profiles.test.agents.LogInvestigator.options]
reasoning_effort = "low"
""".strip() + "\n"


def test_loads_default_target_bindings_into_immutable_models(tmp_path: Path) -> None:
    path = tmp_path / DEFAULT_TARGET_BINDINGS_FILENAME
    path.write_text(VALID_BINDINGS)

    result = load_target_bindings(tmp_path, required=True)

    assert result.ok
    assert result.diagnostics == ()
    assert result.bindings is not None
    assert result.bindings.schema_version == TARGET_BINDINGS_SCHEMA_VERSION
    target = result.bindings.targets["openai"]
    assert target.adapter == "openai"
    assert target.tools["incident.fetch_logs"].values["python"] == "incident_app.tools:fetch_logs"
    assert target.datasources["incident.timeline"].values["python"] == "incident_app.datasources:timeline"
    assert target.external_context["incident_record"].values["python"] == "incident_app.context:current_incident"
    assert target.environments["in_process"].values["provider"].endswith("InProcessEnvironment")
    assert target.profiles["test"].default_model == "test-model"
    assert target.profiles["test"].agents["LogInvestigator"].model == "gpt-5.6-luna"
    assert target.profiles["test"].options["tags"] == ("offline", "deterministic")

    with pytest.raises(TypeError):
        target.tools["other"] = target.tools["incident.fetch_logs"]  # type: ignore[index]
    with pytest.raises(TypeError):
        target.profiles["test"].options["temperature"] = 1.0  # type: ignore[index]


def test_optional_missing_bindings_are_empty_but_explicit_or_required_paths_report_diagnostic(tmp_path: Path) -> None:
    optional = load_target_bindings(tmp_path)
    required = load_target_bindings(tmp_path, required=True)
    explicit = load_target_bindings(tmp_path, "custom.toml")

    assert optional.bindings is None
    assert optional.diagnostics == ()
    assert not optional.ok
    assert [item.code for item in required.diagnostics] == ["TGT002"]
    assert [item.code for item in explicit.diagnostics] == ["TGT002"]
    assert explicit.path == tmp_path / "custom.toml"


def test_explicit_absolute_path_is_not_resolved_under_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = tmp_path / "bindings.toml"
    path.write_text(VALID_BINDINGS)

    result = load_target_bindings(project, path)

    assert result.ok
    assert result.path == path


@pytest.mark.parametrize(
    ("content", "code", "message"),
    [
        ("schema_version = [", "TGT001", "Invalid target bindings TOML"),
        ('schema_version = "999"\n[targets.openai]\nadapter = "openai"\n', "TGT001", "schema_version"),
        ('schema_version = "2"\n', "TGT001", "non-empty `targets`"),
        (
            'schema_version = "2"\n[targets.openai]\nadapter = ""\n',
            "TGT001",
            "adapter",
        ),
        (
            'schema_version = "2"\n[targets.openai]\nadapter = "openai"\nunknown = true\n',
            "TGT003",
            "Unknown key `unknown`",
        ),
        (
            'schema_version = "2"\n[targets.openai]\nadapter = "openai"\n'
            '[targets.openai.environments.local]\nprovider = ""\n',
            "TGT001",
            "provider",
        ),
        (
            'schema_version = "2"\n[targets.openai]\nadapter = "openai"\n'
            '[targets.openai.profiles.production]\nextends = "base"\n',
            "TGT005",
            "cannot declare inheritance",
        ),
    ],
)
def test_reports_structured_shape_and_toml_diagnostics(
    tmp_path: Path,
    content: str,
    code: str,
    message: str,
) -> None:
    (tmp_path / DEFAULT_TARGET_BINDINGS_FILENAME).write_text(content)

    result = load_target_bindings(tmp_path, required=True)

    assert result.bindings is None
    assert code in [item.code for item in result.diagnostics]
    assert any(message in item.message for item in result.diagnostics)


def test_schema_v2_requires_every_target_to_declare_a_named_profile(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_TARGET_BINDINGS_FILENAME).write_text(
        'schema_version = "2"\n[targets.openai]\nadapter = "openai"\n'
    )

    result = load_target_bindings(tmp_path, required=True)

    assert result.bindings is None
    assert [item.code for item in result.diagnostics] == ["TGT001"]
    assert "targets.openai.profiles` must be a non-empty table" in result.diagnostics[0].message


@pytest.mark.parametrize(
    ("suffix", "forbidden_path"),
    [
        (
            '[targets.openai.tools."status.publish"]\npython = "app.tools:publish"\navailability = "enabled"\n',
            "targets.openai.tools.status.publish.availability",
        ),
        (
            '[targets.openai.datasources.timeline]\npython = "app.data:timeline"\nguidance = "Use current data"\n',
            "targets.openai.datasources.timeline.guidance",
        ),
        (
            '[targets.openai.profiles.production.agents.Commander]\nmodel = "model"\ngoal = "Coordinate work"\n',
            "targets.openai.profiles.production.agents.Commander.goal",
        ),
        (
            '[targets.openai.tools.search.options]\nguidance = "Search carefully"\n',
            "targets.openai.tools.search.options.guidance",
        ),
        (
            '[targets.openai.profiles.production.options]\nauthorization = "preapproved"\n',
            "targets.openai.profiles.production.options.authorization",
        ),
    ],
)
def test_rejects_contract_owned_keys_at_every_binding_depth(
    tmp_path: Path,
    suffix: str,
    forbidden_path: str,
) -> None:
    content = (
        'schema_version = "2"\n[targets.openai]\nadapter = "openai"\n'
        '[targets.openai.profiles.production]\ndefault_model = "model"\n'
        + suffix
    )
    (tmp_path / DEFAULT_TARGET_BINDINGS_FILENAME).write_text(content)

    result = load_target_bindings(tmp_path, required=True)

    assert result.bindings is None
    assert [item.code for item in result.diagnostics] == ["TGT004"]
    assert forbidden_path in result.diagnostics[0].message


def test_rejects_contract_owned_keys_at_document_root(tmp_path: Path) -> None:
    content = (
        'schema_version = "2"\n'
        'control = "must be enforced"\n'
        '[targets.openai]\n'
        'adapter = "openai"\n'
        '[targets.openai.profiles.production]\n'
        'default_model = "model"\n'
    )
    (tmp_path / DEFAULT_TARGET_BINDINGS_FILENAME).write_text(content)

    result = load_target_bindings(tmp_path, required=True)

    assert result.bindings is None
    assert [item.code for item in result.diagnostics] == ["TGT004"]
    assert "target-binding document.control" in result.diagnostics[0].message


def test_deterministic_serialization_sorts_names_and_excludes_local_path(tmp_path: Path) -> None:
    content = """
schema_version = "2"

[targets.zeta]
adapter = "zeta"

[targets.zeta.profiles.production]
default_model = "zeta-model"

[targets.alpha]
adapter = "alpha"

[targets.alpha.tools."z.last"]
python = "app:last"
options = { retries = 2, modes = ["safe", "fast"] }

[targets.alpha.tools."a.first"]
python = "app:first"

[targets.alpha.profiles.production]
default_model = "model"
""".strip() + "\n"
    (tmp_path / DEFAULT_TARGET_BINDINGS_FILENAME).write_text(content)
    result = load_target_bindings(tmp_path, required=True)
    assert result.bindings is not None

    data = target_bindings_dict(result.bindings)
    serialized = canonical_target_bindings_json(result.bindings)

    assert list(data["targets"]) == ["alpha", "zeta"]  # type: ignore[arg-type]
    assert list(data["targets"]["alpha"]["tools"]) == ["a.first", "z.last"]  # type: ignore[index]
    assert json.loads(serialized) == data
    assert str(tmp_path) not in serialized
    assert serialized == canonical_target_bindings_json(result.bindings)
