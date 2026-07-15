from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from contract4agents.ir import (
    IR_VERSION,
    AgentIR,
    CanonicalIR,
    CapabilityIR,
    CompositionEdgeIR,
    ControlIR,
    FrozenMap,
    GrantIR,
    GuidanceIR,
    IsolationProfileIR,
    ParameterIR,
    SourceSpan,
    TypeFieldIR,
    TypeIR,
    canonical_ir_data,
    canonical_ir_json,
    contract_digest,
    format_type_ref,
    freeze_json,
    parse_type_ref,
    semantic_id,
)


def test_semantic_ids_are_kind_qualified_and_round_trip() -> None:
    identifier = semantic_id("control", "IncidentCommander", "approval", "status.publish")

    assert str(identifier) == "control:IncidentCommander:approval:status.publish"
    assert type(identifier).parse(str(identifier)) == identifier
    assert identifier.require_kind("control") is identifier

    with pytest.raises(ValueError, match="must have kind"):
        identifier.require_kind("agent")
    with pytest.raises(ValueError, match="Invalid semantic ID part"):
        semantic_id("agent", "bad:name")
    with pytest.raises(ValueError, match="Invalid semantic ID"):
        type(identifier).parse("unknown:value")


@pytest.mark.parametrize(
    ("source", "canonical"),
    [
        ("string", "string"),
        ("IncidentRequest", "type:IncidentRequest"),
        ("type:IncidentRequest?", "type:IncidentRequest?"),
        ("list[ string? ]", "list[string?]"),
        ("map[string,IncidentRequest]", "map[string,type:IncidentRequest]"),
        ("list[map[string, list[type:IncidentRequest]?]]", "list[map[string,list[type:IncidentRequest]?]]"),
    ],
)
def test_type_ref_parser_covers_the_portable_recursive_subset(source: str, canonical: str) -> None:
    assert format_type_ref(parse_type_ref(source)) == canonical


@pytest.mark.parametrize(
    "source",
    [
        "",
        "map[integer,string]",
        "list[string",
        "map[string]",
        "string??",
        "set[string]",
        "type:",
    ],
)
def test_type_ref_parser_rejects_nonportable_or_malformed_types(source: str) -> None:
    with pytest.raises(ValueError):
        parse_type_ref(source)


def test_canonical_ir_serialization_matches_stable_id_and_type_rules() -> None:
    ir = _sample_ir(SourceSpan("agents/incident.contract", 10, 3))

    data = canonical_ir_data(ir)
    encoded = canonical_ir_json(ir)
    agents = cast(dict[str, object], data["agents"])
    commander = cast(dict[str, object], agents["agent:IncidentCommander"])
    types = cast(dict[str, object], data["types"])
    request = cast(dict[str, object], types["type:IncidentRequest"])

    assert data["ir_version"] == IR_VERSION == "2"
    assert list(agents) == ["agent:IncidentCommander", "agent:LogInvestigator"]
    assert commander["output_type"] == "type:IncidentDecision"
    assert commander["description"] == "Coordinates the incident response."
    assert "assertions" not in commander
    assert request["fields"] == [
        {
            "default": None,
            "has_default": False,
            "name": "incident_id",
            "type_ref": "string",
        }
    ]
    assert "id" not in commander
    assert "span" not in commander
    assert "agents/incident.contract" not in encoded
    assert "timestamp" not in encoded
    assert '": ' not in encoded
    assert ", " not in encoded
    assert encoded.startswith('{"agents":')


def test_contract_digest_excludes_spans_and_input_order_but_tracks_semantics() -> None:
    first = _sample_ir(SourceSpan("agents/incident.contract", 10, 3), reverse=False)
    second = _sample_ir(SourceSpan("moved/incident.contract", 900, 12), reverse=True)

    assert canonical_ir_json(first) == canonical_ir_json(second)
    assert contract_digest(first) == contract_digest(second)
    assert contract_digest(first).startswith("sha256:")
    assert len(contract_digest(first)) == 71

    changed_agent = AgentIR(
        id=semantic_id("agent", "IncidentCommander"),
        name="IncidentCommander",
        parameters=(ParameterIR("request", parse_type_ref("IncidentRequest")),),
        output_type=parse_type_ref("IncidentDecision"),
        goal="A different semantic goal.",
    )
    changed = CanonicalIR.create(agents=(changed_agent,))
    assert contract_digest(changed) != contract_digest(first)


def test_ir_entities_and_collections_are_immutable() -> None:
    ir = _sample_ir(SourceSpan("agents/incident.contract", 1))

    with pytest.raises(FrozenInstanceError):
        ir.ir_version = "3"  # type: ignore[misc]
    with pytest.raises(TypeError):
        ir.agents[semantic_id("agent", "Other")] = ir.agents[semantic_id("agent", "IncidentCommander")]  # type: ignore[index]
    with pytest.raises(AttributeError):
        ir.agents.items_tuple().append((semantic_id("agent", "Other"), object()))  # type: ignore[attr-defined]


def test_ir_rejects_absolute_source_paths_and_non_json_defaults() -> None:
    with pytest.raises(ValueError, match="repository-relative"):
        SourceSpan("/Users/example/project/agent.contract", 1)
    with pytest.raises(ValueError, match="normalized POSIX"):
        SourceSpan("agents/./agent.contract", 1)
    with pytest.raises(TypeError, match="not canonical JSON"):
        freeze_json(lambda: None)
    with pytest.raises(ValueError, match="NaN"):
        freeze_json(float("nan"))


def test_ir_construction_rejects_duplicate_ids_and_mismatched_names() -> None:
    first = TypeIR(semantic_id("type", "A"), "A", ())
    second = TypeIR(semantic_id("type", "A"), "A", ())

    with pytest.raises(ValueError, match="Duplicate frozen-map key"):
        CanonicalIR.create(types=(first, second))
    with pytest.raises(ValueError, match="does not match"):
        TypeIR(semantic_id("type", "A"), "B", ())


def test_grant_isolation_reference_requires_an_isolation_id() -> None:
    with pytest.raises(ValueError, match="must have kind isolation"):
        GrantIR(
            id=semantic_id("grant", "Agent", "tool"),
            agent_id=semantic_id("agent", "Agent"),
            capability_id=semantic_id("tool", "tool"),
            availability="enabled",
            authorization="preapproved",
            execution="host",
            isolation_id=semantic_id("agent", "NotIsolation"),
        )


def test_canonical_serializer_fails_closed_on_an_injected_native_object() -> None:
    invalid = CapabilityIR(
        id=semantic_id("tool", "test.bad"),
        name="test.bad",
        kind="tool",
        parameters=(),
        output_type=parse_type_ref("string"),
        description=cast(str, object()),
        side_effect=False,
    )

    with pytest.raises(TypeError, match="cannot contain values of type object"):
        canonical_ir_json(CanonicalIR.create(capabilities=(invalid,)))


def _sample_ir(span: SourceSpan, *, reverse: bool = False) -> CanonicalIR:
    request_type = TypeIR(
        semantic_id("type", "IncidentRequest"),
        "IncidentRequest",
        (TypeFieldIR("incident_id", parse_type_ref("string"), span=span),),
        span=span,
    )
    decision_type = TypeIR(
        semantic_id("type", "IncidentDecision"),
        "IncidentDecision",
        (TypeFieldIR("summary", parse_type_ref("string")),),
    )
    finding_type = TypeIR(
        semantic_id("type", "LogFinding"),
        "LogFinding",
        (TypeFieldIR("summary", parse_type_ref("string")),),
    )
    tool = CapabilityIR(
        id=semantic_id("tool", "status.publish"),
        name="status.publish",
        kind="tool",
        parameters=(ParameterIR("decision", parse_type_ref("IncidentDecision")),),
        output_type=parse_type_ref("IncidentDecision"),
        description="Publish an approved status update.",
        side_effect=True,
        span=span,
    )
    grant_id = semantic_id("grant", "IncidentCommander", "status.publish")
    grant = GrantIR(
        id=grant_id,
        agent_id=semantic_id("agent", "IncidentCommander"),
        capability_id=tool.id,
        availability="enabled",
        authorization="approval_required",
        execution="host",
        isolation_id=semantic_id("isolation", "EvidenceWorker"),
        span=span,
    )
    commander = AgentIR(
        id=semantic_id("agent", "IncidentCommander"),
        name="IncidentCommander",
        parameters=(ParameterIR("request", parse_type_ref("IncidentRequest")),),
        output_type=parse_type_ref("IncidentDecision"),
        goal="Form an evidence-backed incident decision.",
        description="Coordinates the incident response.",
        guidance=(GuidanceIR("Delegate technical evidence collection when needed."),),
        grant_ids=(grant_id,),
        span=span,
    )
    investigator = AgentIR(
        id=semantic_id("agent", "LogInvestigator"),
        name="LogInvestigator",
        parameters=(ParameterIR("request", parse_type_ref("IncidentRequest")),),
        output_type=parse_type_ref("LogFinding"),
        goal="Find the most likely cause supported by log evidence.",
    )
    isolation = IsolationProfileIR(
        id=semantic_id("isolation", "EvidenceWorker"),
        name="EvidenceWorker",
        context="explicit_only",
        capabilities="declared_only",
        state="fresh",
        filesystem="none",
        network="denied",
        secrets="none",
        return_channel="final_output_only",
    )
    edge = CompositionEdgeIR(
        id=semantic_id("edge", "investigate_logs"),
        name="investigate_logs",
        source_agent_id=commander.id,
        target_agent_id=investigator.id,
        mode="delegate",
        description="Investigate when log evidence is needed.",
        history="none",
        input_mappings=FrozenMap({"request": "input.request"}),
        isolation_id=isolation.id,
    )
    control = ControlIR(
        id=semantic_id("control", "IncidentCommander", "approval", "status.publish"),
        name="approval_status_publish",
        agent_id=commander.id,
        severity="high",
        required=True,
        audience=("adapter", "host", "evaluator", "reviewer"),
        assessment="runtime",
        derived_from=grant.id,
        expected_evidence=("approval.requested", "approval.completed", "tool.started"),
    )
    types = (request_type, decision_type, finding_type)
    agents = (commander, investigator)
    if reverse:
        types = tuple(reversed(types))
        agents = tuple(reversed(agents))
    return CanonicalIR.create(
        types=types,
        capabilities=(tool,),
        agents=agents,
        grants=(grant,),
        composition=(edge,),
        controls=(control,),
        isolation_profiles=(isolation,),
    )
