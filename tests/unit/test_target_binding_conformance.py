from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from contract4agents.adapters.openai import (
    openai_target_binding_validator,
    openai_target_profile_validator,
)
from contract4agents.diagnostics import Diagnostic
from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    CapabilityIR,
    ContextRequirementIR,
    ExternalContextIR,
    GrantIR,
    ParameterIR,
    parse_type_ref,
    semantic_id,
)
from contract4agents.target_bindings import (
    AgentProfile,
    BindingEntry,
    TargetBinding,
    TargetBindings,
    TargetProfile,
    validate_target_binding_conformance,
)

MODULE_NAME = "target_binding_test_app"


def test_conformance_resolves_required_bindings_without_calling_application_code(tmp_path: Path) -> None:
    _write_application_module(tmp_path)
    bindings = _bindings(
        tmp_path,
        tools={"incident.fetch_logs": f"{MODULE_NAME}:fetch_logs"},
        datasources={"incident.timeline": f"{MODULE_NAME}:timeline"},
        external_context={"incident_record": f"{MODULE_NAME}:current_incident"},
    )

    try:
        result = validate_target_binding_conformance(_canonical_ir(), bindings, "openai")
        with _import_path(tmp_path):
            module = importlib.import_module(MODULE_NAME)

        assert result.ok
        assert result.diagnostics == ()
        assert module.CALLS == 0
        assert [item.semantic_id for item in result.implementations] == [
            "datasource:incident.timeline",
            "external:incident_record",
            "tool:incident.fetch_logs",
        ]
        tool = next(item for item in result.implementations if item.section == "tools")
        assert [(item.name, item.required) for item in tool.parameters] == [("query", True), ("limit", False)]
        payload = result.to_dict()
        assert json.loads(result.to_json()) == payload
        assert result.to_json() == validate_target_binding_conformance(_canonical_ir(), bindings, "openai").to_json()
        assert "function" not in result.to_json()
    finally:
        sys.modules.pop(MODULE_NAME, None)


def test_conformance_reports_missing_and_stale_entries_without_importing_stale_locator(tmp_path: Path) -> None:
    bindings = _bindings(
        tmp_path,
        tools={
            "incident.fetch_logs": None,
            "stale.tool": "module_that_must_not_be_imported:missing",
        },
    )

    result = validate_target_binding_conformance(_canonical_ir(), bindings, "openai")

    assert not result.ok
    assert [(item.code, item.message) for item in result.diagnostics] == [
        (
            "TGT102",
            "Target `openai` is missing `datasources` binding `incident.timeline` required by canonical IR",
        ),
        (
            "TGT102",
            "Target `openai` is missing `external_context` binding `incident_record` required by canonical IR",
        ),
        (
            "TGT103",
            "Target `openai` has stale `tools` binding `stale.tool` not required by canonical IR",
        ),
    ]
    assert not any(item.code == "TGT105" for item in result.diagnostics)


@pytest.mark.parametrize(
    ("locator", "expected_code"),
    [
        (42, "TGT104"),
        ("not-a-locator", "TGT104"),
        ("missing_target_binding_module:lookup", "TGT105"),
        (f"{MODULE_NAME}:NOT_CALLABLE", "TGT106"),
    ],
)
def test_conformance_rejects_invalid_imports_and_noncallables(
    tmp_path: Path,
    locator: object,
    expected_code: str,
) -> None:
    _write_application_module(tmp_path)
    bindings = _bindings(
        tmp_path,
        tools={"incident.fetch_logs": locator},
        datasources={"incident.timeline": None},
        external_context={"incident_record": None},
    )

    try:
        result = validate_target_binding_conformance(_canonical_ir(), bindings, "openai")

        assert [item.code for item in result.diagnostics] == [expected_code]
        assert not result.ok
    finally:
        sys.modules.pop(MODULE_NAME, None)


def test_conformance_rejects_a_loaded_host_module_without_replacing_it(tmp_path: Path) -> None:
    host_calendar = importlib.import_module("calendar")
    before = {
        name: module
        for name, module in sys.modules.items()
        if name == "calendar" or name.startswith("calendar.")
    }
    (tmp_path / "calendar.py").write_text("def fetch_logs(query, limit=10):\n    return query\n", encoding="utf-8")
    bindings = _bindings(
        tmp_path,
        tools={"incident.fetch_logs": "calendar:fetch_logs"},
        datasources={"incident.timeline": None},
        external_context={"incident_record": None},
    )

    result = validate_target_binding_conformance(_canonical_ir(), bindings, "openai")

    assert [item.code for item in result.diagnostics] == ["TGT105"]
    assert "unique, package-qualified application module name" in result.diagnostics[0].message
    assert sys.modules["calendar"] is host_calendar
    assert {
        name: module
        for name, module in sys.modules.items()
        if name == "calendar" or name.startswith("calendar.")
    } == before


@pytest.mark.parametrize(
    ("attribute", "message"),
    [
        ("wrong_names", "parameter names"),
        ("wrong_requiredness", "parameter requiredness"),
    ],
)
def test_conformance_compares_callable_parameter_names_and_requiredness(
    tmp_path: Path,
    attribute: str,
    message: str,
) -> None:
    _write_application_module(tmp_path)
    bindings = _bindings(
        tmp_path,
        tools={"incident.fetch_logs": f"{MODULE_NAME}:{attribute}"},
        datasources={"incident.timeline": None},
        external_context={"incident_record": None},
    )

    try:
        result = validate_target_binding_conformance(_canonical_ir(), bindings, "openai")

        assert [item.code for item in result.diagnostics] == ["TGT107"]
        assert message in result.diagnostics[0].message
        assert not result.ok
    finally:
        sys.modules.pop(MODULE_NAME, None)


def test_conformance_requires_only_enabled_tools_and_referenced_external_context(tmp_path: Path) -> None:
    bindings = _bindings(
        tmp_path,
        tools={"incident.fetch_logs": None},
        datasources={"incident.timeline": None},
        external_context={"incident_record": None},
    )

    result = validate_target_binding_conformance(_canonical_ir(), bindings, "openai")

    assert result.ok
    assert result.diagnostics == ()


def test_conformance_reports_unknown_target(tmp_path: Path) -> None:
    result = validate_target_binding_conformance(_canonical_ir(), _bindings(tmp_path), "missing")

    assert not result.ok
    assert [item.code for item in result.diagnostics] == ["TGT101"]
    assert result.implementations == ()


def test_conformance_requires_complete_profiles_without_unknown_agent_overrides(tmp_path: Path) -> None:
    bindings = _bindings(
        tmp_path,
        tools={"incident.fetch_logs": None},
        datasources={"incident.timeline": None},
        external_context={"incident_record": None},
        profiles={
            "incomplete": TargetProfile(
                agents={"RemovedAgent": AgentProfile(model="stale-model")}
            ),
            "complete": TargetProfile(default_model="default-model"),
        },
    )

    result = validate_target_binding_conformance(_canonical_ir(), bindings, "openai")

    assert [item.code for item in result.diagnostics] == ["TGT108", "TGT109"]
    assert "RemovedAgent" in result.diagnostics[0].message
    assert "IncidentCommander" in result.diagnostics[1].message


def test_conformance_validates_model_factory_signature_without_calling_it(
    tmp_path: Path,
) -> None:
    _write_application_module(tmp_path)
    bindings = _bindings(
        tmp_path,
        tools={"incident.fetch_logs": None},
        datasources={"incident.timeline": None},
        external_context={"incident_record": None},
        profiles={
            "test": TargetProfile(
                default_model="test-model",
                options={"model_factory": f"{MODULE_NAME}:model_factory"},
            )
        },
    )

    try:
        result = validate_target_binding_conformance(
            _canonical_ir(),
            bindings,
            "openai",
            project_root=tmp_path,
        )
        with _import_path(tmp_path):
            module = importlib.import_module(MODULE_NAME)

        assert result.ok
        assert module.CALLS == 0
    finally:
        sys.modules.pop(MODULE_NAME, None)


@pytest.mark.parametrize(
    ("locator", "code"),
    [
        ("not-a-locator", "TGT112"),
        ("missing_model_factory_module:create", "TGT113"),
        (f"{MODULE_NAME}:NOT_CALLABLE", "TGT114"),
        (f"{MODULE_NAME}:model_factory_with_required_extra", "TGT114"),
        (f"{MODULE_NAME}:positional_model_factory", "TGT114"),
    ],
)
def test_conformance_rejects_invalid_model_factories(
    tmp_path: Path,
    locator: str,
    code: str,
) -> None:
    _write_application_module(tmp_path)
    bindings = _bindings(
        tmp_path,
        tools={"incident.fetch_logs": None},
        datasources={"incident.timeline": None},
        external_context={"incident_record": None},
        profiles={
            "test": TargetProfile(
                default_model="test-model",
                options={"model_factory": locator},
            )
        },
    )

    try:
        result = validate_target_binding_conformance(
            _canonical_ir(),
            bindings,
            "openai",
            project_root=tmp_path,
        )
    finally:
        sys.modules.pop(MODULE_NAME, None)

    assert [item.code for item in result.diagnostics] == [code]


def test_conformance_invokes_adapter_profile_validator(tmp_path: Path) -> None:
    bindings = _bindings(
        tmp_path,
        tools={"incident.fetch_logs": None},
        datasources={"incident.timeline": None},
        external_context={"incident_record": None},
    )
    seen: list[tuple[str, Path]] = []

    def profile_validator(
        ir: CanonicalIR,
        target_name: str,
        target: TargetBinding,
        project_root: Path,
    ) -> tuple[Diagnostic, ...]:
        assert ir is not None
        assert target.adapter == "openai"
        seen.append((target_name, project_root))
        return ()

    result = validate_target_binding_conformance(
        _canonical_ir(),
        bindings,
        "openai",
        project_root=tmp_path,
        profile_validator=profile_validator,
    )

    assert result.ok
    assert seen == [("openai", tmp_path.resolve())]


@pytest.mark.parametrize(
    ("section", "entry", "expected_code"),
    [
        (
            "tools",
            BindingEntry(
                {
                    "python": f"{MODULE_NAME}:fetch_logs",
                    "provider": "openai",
                    "tool": "web_search",
                }
            ),
            "TGT110",
        ),
        (
            "tools",
            BindingEntry(
                {
                    "provider": "openai",
                    "tool": "web_search",
                    "provider_tool": "file_search",
                }
            ),
            "TGT110",
        ),
        (
            "tools",
            BindingEntry({"endpoint": "https://example.test/tool"}),
            "TGT111",
        ),
        (
            "tools",
            BindingEntry({"typescript": "app/tools:fetch_logs"}),
            "TGT111",
        ),
        (
            "tools",
            BindingEntry({"provider": "openai", "tool": "file_search"}),
            "TGT111",
        ),
        (
            "datasources",
            BindingEntry({"provider": "openai", "tool": "web_search"}),
            "TGT111",
        ),
        (
            "external_context",
            BindingEntry({"provider": "openai", "tool": "web_search"}),
            "TGT111",
        ),
    ],
)
def test_openai_conformance_rejects_ambiguous_or_unsupported_locators(
    tmp_path: Path,
    section: str,
    entry: BindingEntry,
    expected_code: str,
) -> None:
    _write_application_module(tmp_path)
    target = TargetBinding(
        adapter="openai",
        tools={
            "incident.fetch_logs": (
                entry
                if section == "tools"
                else BindingEntry({"python": f"{MODULE_NAME}:fetch_logs"})
            )
        },
        datasources={
            "incident.timeline": (
                entry
                if section == "datasources"
                else BindingEntry({"python": f"{MODULE_NAME}:timeline"})
            )
        },
        external_context={
            "incident_record": (
                entry
                if section == "external_context"
                else BindingEntry({"python": f"{MODULE_NAME}:current_incident"})
            )
        },
        profiles={"test": TargetProfile(default_model="test-model")},
    )
    bindings = TargetBindings(
        tmp_path / "contract4agents.targets.toml",
        {"openai": target},
    )

    try:
        result = validate_target_binding_conformance(
            _canonical_ir(),
            bindings,
            "openai",
            adapter_validator=openai_target_binding_validator,
        )
    finally:
        sys.modules.pop(MODULE_NAME, None)

    assert [item.code for item in result.diagnostics] == [expected_code]


def test_openai_conformance_accepts_canonical_web_search_binding(tmp_path: Path) -> None:
    _write_application_module(tmp_path)
    target = TargetBinding(
        adapter="openai",
        tools={
            "incident.fetch_logs": BindingEntry(
                {"provider": "openai", "tool": "web_search"}
            )
        },
        datasources={
            "incident.timeline": BindingEntry(
                {"python": f"{MODULE_NAME}:timeline"}
            )
        },
        external_context={
            "incident_record": BindingEntry(
                {"python": f"{MODULE_NAME}:current_incident"}
            )
        },
        profiles={"test": TargetProfile(default_model="test-model")},
    )
    bindings = TargetBindings(
        tmp_path / "contract4agents.targets.toml",
        {"openai": target},
    )

    try:
        result = validate_target_binding_conformance(
            _canonical_ir(),
            bindings,
            "openai",
            adapter_validator=openai_target_binding_validator,
        )
    finally:
        sys.modules.pop(MODULE_NAME, None)

    assert result.ok
    assert result.diagnostics == ()


def test_programmatic_binding_conformance_rejects_nested_credentials(tmp_path: Path) -> None:
    bindings = _bindings(
        tmp_path,
        profiles={
            "test": TargetProfile(
                default_model="test-model",
                options={"transport": {"client_secret": "secret-value"}},
            )
        },
    )

    result = validate_target_binding_conformance(
        _canonical_ir(),
        bindings,
        "openai",
    )

    diagnostic = next(item for item in result.diagnostics if item.code == "TGT115")
    assert "profiles.test.options.transport.client_secret" in diagnostic.message
    assert "secret-value" not in diagnostic.message


def test_openai_conformance_rejects_model_factory(tmp_path: Path) -> None:
    _write_application_module(tmp_path)
    bindings = _bindings(
        tmp_path,
        tools={"incident.fetch_logs": None},
        datasources={"incident.timeline": None},
        external_context={"incident_record": None},
        profiles={
            "test": TargetProfile(
                default_model="test-model",
                options={"model_factory": f"{MODULE_NAME}:model_factory"},
            )
        },
    )

    try:
        result = validate_target_binding_conformance(
            _canonical_ir(),
            bindings,
            "openai",
            project_root=tmp_path,
            profile_validator=openai_target_profile_validator,
        )
    finally:
        sys.modules.pop(MODULE_NAME, None)

    assert [item.code for item in result.diagnostics] == ["TGT115"]


def _canonical_ir() -> CanonicalIR:
    fetch_logs = CapabilityIR(
        id=semantic_id("tool", "incident.fetch_logs"),
        name="incident.fetch_logs",
        kind="tool",
        parameters=(
            ParameterIR("query", parse_type_ref("string")),
            ParameterIR("limit", parse_type_ref("integer"), required=False, has_default=True, default=10),
        ),
        output_type=parse_type_ref("string"),
        description="Fetch incident logs.",
        side_effect=False,
    )
    denied_tool = CapabilityIR(
        id=semantic_id("tool", "incident.delete"),
        name="incident.delete",
        kind="tool",
        parameters=(),
        output_type=parse_type_ref("boolean"),
        description="Delete incident evidence.",
        side_effect=True,
    )
    timeline = CapabilityIR(
        id=semantic_id("datasource", "incident.timeline"),
        name="incident.timeline",
        kind="datasource",
        parameters=(ParameterIR("incident_id", parse_type_ref("string")),),
        output_type=parse_type_ref("string"),
        description="Resolve the incident timeline.",
        render="markdown",
        cache="run",
    )
    enabled_grant = GrantIR(
        id=semantic_id("grant", "IncidentCommander", "incident.fetch_logs"),
        agent_id=semantic_id("agent", "IncidentCommander"),
        capability_id=fetch_logs.id,
        availability="enabled",
        authorization="preapproved",
        execution="host",
    )
    denied_grant = GrantIR(
        id=semantic_id("grant", "IncidentCommander", "incident.delete"),
        agent_id=semantic_id("agent", "IncidentCommander"),
        capability_id=denied_tool.id,
        availability="denied",
    )
    incident_record = ExternalContextIR(
        id=semantic_id("external", "incident_record"),
        name="incident_record",
        output_type=parse_type_ref("string"),
        description="Current incident record.",
        sensitivity="internal",
        render="markdown",
    )
    unused_external = ExternalContextIR(
        id=semantic_id("external", "unused_context"),
        name="unused_context",
        output_type=parse_type_ref("string"),
        description="Unused context.",
        sensitivity="internal",
        render="markdown",
    )
    context = ContextRequirementIR(
        id=semantic_id("context", "IncidentCommander", "incident"),
        agent_id=semantic_id("agent", "IncidentCommander"),
        name="incident",
        type_ref=parse_type_ref("string"),
        origin="external",
        origin_id=incident_record.id,
    )
    agent = AgentIR(
        id=semantic_id("agent", "IncidentCommander"),
        name="IncidentCommander",
        parameters=(),
        output_type=parse_type_ref("string"),
        goal="Coordinate the incident.",
        grant_ids=(enabled_grant.id, denied_grant.id),
        context_ids=(context.id,),
    )
    return CanonicalIR.create(
        capabilities=(fetch_logs, denied_tool, timeline),
        external_contexts=(incident_record, unused_external),
        contexts=(context,),
        grants=(enabled_grant, denied_grant),
        agents=(agent,),
    )


def _bindings(
    root: Path,
    *,
    tools: dict[str, object | None] | None = None,
    datasources: dict[str, object | None] | None = None,
    external_context: dict[str, object | None] | None = None,
    profiles: dict[str, TargetProfile] | None = None,
) -> TargetBindings:
    return TargetBindings(
        path=root / "contract4agents.targets.toml",
        targets={
            "openai": TargetBinding(
                adapter="openai",
                tools=_entries(tools or {}),
                datasources=_entries(datasources or {}),
                external_context=_entries(external_context or {}),
                profiles=profiles or {"test": TargetProfile(default_model="test-model")},
            )
        },
    )


def _entries(values: dict[str, object | None]) -> dict[str, BindingEntry]:
    return {
        name: BindingEntry({"provider": "native"} if locator is None else {"python": locator})
        for name, locator in values.items()
    }


def _write_application_module(root: Path) -> None:
    (root / f"{MODULE_NAME}.py").write_text(
        """
CALLS = 0
NOT_CALLABLE = 42

def _called():
    global CALLS
    CALLS += 1
    raise AssertionError("binding conformance must never call application code")

def fetch_logs(query, limit=10):
    return _called()

def timeline(incident_id):
    return _called()

def current_incident():
    return _called()

def wrong_names(text, limit=10):
    return _called()

def wrong_requiredness(query, limit):
    return _called()

def model_factory(*, model, options):
    return _called()

def model_factory_with_required_extra(*, model, options, region):
    return _called()

def positional_model_factory(model, /, options):
    return _called()
""".lstrip()
    )


@contextmanager
def _import_path(root: Path) -> Iterator[None]:
    sys.path.insert(0, str(root))
    try:
        yield
    finally:
        sys.path.remove(str(root))
