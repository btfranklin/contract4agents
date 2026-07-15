from __future__ import annotations

from pathlib import Path

from contract4agents.ir import build_canonical_ir, canonical_ir_json, contract_digest, semantic_id
from contract4agents.parser import parse_project
from contract4agents.semantics import analyze_project


def test_v2_source_surface_parses_and_is_semantically_valid(tmp_path: Path) -> None:
    source = tmp_path / "incident.contract"
    source.write_text(
        """\
type IncidentRequest:
    incident_id: string

type IncidentRecord:
    incident_id: string

type LogBatch:
    entries: list[string]

type LogFinding:
    summary: string

tool incident.fetch_logs(request: IncidentRequest) -> LogBatch:
    description = "Fetch incident logs."
    side_effect = false

datasource incident.timeline(incident: IncidentRecord) -> LogBatch:
    description = "Resolve an incident timeline."
    render = markdown
    cache = run

external_context incident_record -> IncidentRecord:
    description = "The current incident record."
    sensitivity = confidential
    render = markdown

isolation EvidenceWorker:
    context = explicit_only
    capabilities = declared_only
    state = fresh
    filesystem = none
    network = denied
    secrets = none
    return = final_output_only

agent LogInvestigator(request: IncidentRequest) -> LogFinding:
    use incident.fetch_logs:
        availability = enabled
        authorization = preapproved
        execution = host
    context incident: IncidentRecord from external incident_record
    context timeline: LogBatch from datasource incident.timeline:
        map incident = context.incident
    goal = "Find the cause."
    guidance = ["Cite evidence."]

agent IncidentCommander(request: IncidentRequest) -> LogFinding:
    goal = "Coordinate the investigation."
    guidance = ["Delegate evidence collection."]

composition investigate_logs from IncidentCommander to LogInvestigator:
    mode = delegate
    description = "Investigate when logs are needed."
    history = none
    map request = input.request
    isolation = EvidenceWorker

control evidence_required for IncidentCommander:
    severity = high
    required = true
    audience = [host, evaluator, reviewer]
    assessment = post_run
    require = trace.agent_called(LogInvestigator)

quality evidence_backed for IncidentCommander:
    rubric = "The conclusion is supported by evidence."
    audience = [evaluator, reviewer]

operational_control latency for IncidentCommander:
    severity = medium
    require = trace.duration < 10s

eval delegates_for_evidence for IncidentCommander:
    given request = IncidentRequest.fixture("clear")
    expect trace.agent_called(LogInvestigator)
"""
    )

    project = parse_project(tmp_path)
    result = analyze_project(project)

    assert result.ok, [diagnostic.format() for diagnostic in result.diagnostics]
    assert set(project.tools) == {"incident.fetch_logs"}
    assert set(project.datasources) == {"incident.timeline"}
    assert set(project.external_contexts) == {"incident_record"}
    assert set(project.isolations) == {"EvidenceWorker"}
    assert set(project.compositions) == {"investigate_logs"}
    assert project.agents["LogInvestigator"].grants[0].authorization == "preapproved"
    assert project.agents["LogInvestigator"].context[0].source == "incident_record"
    assert project.agents["LogInvestigator"].context[1].mappings == {"incident": "context.incident"}
    assert project.compositions["investigate_logs"].mappings == {"request": "input.request"}
    assert project.controls[0].name == "evidence_required"
    assert project.qualities[0].name == "evidence_backed"
    assert project.operational_controls[0].name == "latency"

    ir = build_canonical_ir(project)
    assert ir.agents[semantic_id("agent", "IncidentCommander")].goal == "Coordinate the investigation."
    assert semantic_id("control", "LogInvestigator", "output_conformance") in ir.controls
    assert semantic_id("control", "IncidentCommander", "evidence_required") in ir.controls
    assert ir.contexts[semantic_id("context", "LogInvestigator", "timeline")].input_mappings == {
        "incident": "context.incident"
    }
    assert canonical_ir_json(ir) == canonical_ir_json(build_canonical_ir(project))
    assert contract_digest(ir).startswith("sha256:")


def test_v2_enabled_grant_requires_explicit_authorization_and_execution(tmp_path: Path) -> None:
    (tmp_path / "invalid.contract").write_text(
        """\
type Request:
    value: string

type Result:
    value: string

tool lookup(request: Request) -> Result:
    description = "Look up a value."
    side_effect = false

agent Worker(request: Request) -> Result:
    use lookup:
        availability = enabled
    goal = "Look up a value."
"""
    )

    result = analyze_project(parse_project(tmp_path))

    assert [(item.code, item.message) for item in result.diagnostics] == [
        ("SEM107", "Enabled grant `Worker:lookup` requires explicit authorization"),
        ("SEM108", "Enabled grant `Worker:lookup` requires a valid execution boundary"),
    ]


def test_v2_required_composition_inputs_and_isolation_fail_closed_semantically(tmp_path: Path) -> None:
    (tmp_path / "invalid.contract").write_text(
        """\
type Request:
    value: string

type Result:
    value: string

agent Parent(request: Request) -> Result:
    goal = "Delegate."

agent Child(request: Request) -> Result:
    goal = "Return a result."

composition broken from Parent to Child:
    mode = delegate
    description = "Broken edge."
    history = none
    isolation = MissingProfile
"""
    )

    result = analyze_project(parse_project(tmp_path))

    assert [(item.code, item.message) for item in result.diagnostics] == [
        ("SEM119", "Composition `broken` references unknown isolation `MissingProfile`"),
        ("SEM121", "Composition `broken` is missing target input mappings: request"),
    ]


def test_v2_datasource_context_requires_complete_typed_input_mappings(tmp_path: Path) -> None:
    (tmp_path / "invalid.contract").write_text(
        """\
type Request:
    value: string

type Result:
    value: string

datasource lookup(query: string, limit: integer) -> Result:
    description = "Resolve a value."
    render = markdown
    cache = run

agent Worker(request: Request) -> Result:
    context value: Result from datasource lookup:
        map query = input.request
        map stale = input.request.value
    goal = "Resolve a value."
"""
    )

    result = analyze_project(parse_project(tmp_path))

    assert [(item.code, item.message) for item in result.diagnostics] == [
        ("SEM140", "Context `Worker.value` maps unknown datasource inputs: stale"),
        ("SEM141", "Context `Worker.value` is missing datasource input mappings: limit"),
        (
            "SEM143",
            "Context `Worker.value` maps `query` from `Request` but the datasource requires `string`",
        ),
    ]
