from __future__ import annotations

from pathlib import Path

import pytest

from contract4agents.compiler import build_artifacts, compile_project
from contract4agents.diagnostics import ContractError
from contract4agents.ir import build_canonical_ir, semantic_id
from contract4agents.parser import parse_project
from contract4agents.semantics import analyze_project

ROOT = Path(__file__).resolve().parents[2]
INCIDENT = ROOT / "examples" / "incident-command"


def test_public_example_uses_canonical_source_semantics() -> None:
    project = parse_project(INCIDENT)
    result = analyze_project(project)

    assert result.ok, [item.format() for item in result.diagnostics]
    assert set(project.tools) == {"deploys.list", "logs.search", "metrics.query", "status_page.draft_update"}
    assert set(project.compositions) == {
        "inspect_deploys",
        "investigate_logs",
        "measure_impact",
        "rewrite_customer_impact",
    }
    assert project.agents["IncidentCommander"].grants[-1].authorization == "approval_required"


def test_parser_builds_shared_capabilities_grants_context_and_assurance(tmp_path: Path) -> None:
    source = tmp_path / "surface.contract"
    source.write_text(
        """\
type Request:
    id: string

type Record:
    value: string

type Result:
    ok: boolean

tool records.lookup(request: Request) -> Record:
    description = "Look up a record."
    side_effect = false

datasource records.current(request: Request) -> Record:
    description = "Resolve the current record."
    render = json
    cache = run

external_context tenant_record -> Record:
    description = "Host-owned tenant record."
    sensitivity = confidential
    render = json

agent Worker(request: Request) -> Result:
    use records.lookup:
        availability = enabled
        authorization = preapproved
        execution = host
    context record: Record from external tenant_record
    goal = "Return a verified result."
    guidance = ["Use declared evidence."]

control evidence_required for Worker:
    severity = high
    required = true
    audience = [host, evaluator, reviewer]
    assessment = post_run
    require = trace.tool_called(records.lookup)

quality evidence_backed for Worker:
    rubric = "The result is supported by the record."
    audience = [evaluator, reviewer]
"""
    )

    project = parse_project(tmp_path)
    result = analyze_project(project)
    ir = build_canonical_ir(project)

    assert result.ok, [item.format() for item in result.diagnostics]
    assert project.datasources["records.current"].return_type == "Record"
    assert semantic_id("tool", "records.lookup") in ir.capabilities
    assert semantic_id("control", "Worker", "evidence_required") in ir.controls
    assert semantic_id("quality", "Worker", "evidence_backed") in ir.qualities


def test_unknown_agent_attributes_report_the_current_surface(tmp_path: Path) -> None:
    (tmp_path / "invalid.contract").write_text(
        "type Result:\n    ok: boolean\n\nagent A() -> Result:\n    typoed_attribute = true\n"
    )

    result = analyze_project(parse_project(tmp_path))

    assert [(item.code, item.message) for item in result.diagnostics] == [
        ("SEM070", "Unknown agent attribute `typoed_attribute` on `A`")
    ]


def test_compile_project_is_the_canonical_compiler(tmp_path: Path) -> None:
    artifacts = compile_project(INCIDENT, tmp_path / "build")

    assert artifacts.ir.agents[semantic_id("agent", "IncidentCommander")].name == "IncidentCommander"
    assert artifacts.contract_digest.startswith("sha256:")
    assert "IncidentBrief" in artifacts.schemas
    assert (tmp_path / "build" / "ir" / "contract.json").is_file()
    assert (tmp_path / "build" / "schemas" / "IncidentBrief.json").is_file()
    assert (tmp_path / "build" / "generated" / "python" / "models.py").is_file()


def test_build_artifacts_accepts_canonical_ir_only() -> None:
    project = parse_project(INCIDENT)
    artifacts = build_artifacts(build_canonical_ir(project))

    assert "IncidentCommander" in artifacts.instructions
    assert "summary.md" in {str(path) for path in artifacts.docs}


def test_compiler_emits_standalone_and_referenced_enum_schemas(tmp_path: Path) -> None:
    (tmp_path / "enum.contract").write_text(
        """\
enum Status:
    "accepted"
    "failed"

type Result:
    status: Status
"""
    )

    artifacts = compile_project(tmp_path)

    assert artifacts.schemas["Status"] == {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:contract4agents:type:Status",
        "title": "Status",
        "type": "string",
        "enum": ["accepted", "failed"],
    }
    assert artifacts.schemas["Result"]["$defs"] == {
        "Status": {"title": "Status", "type": "string", "enum": ["accepted", "failed"]}
    }
    assert "`Status` enum: `accepted`, `failed`" in artifacts.docs[next(iter(artifacts.docs))]


def test_compile_check_detects_stale_artifacts(tmp_path: Path) -> None:
    build = tmp_path / "build"
    compile_project(INCIDENT, build)
    (build / "ir" / "contract.json").write_text("{}\n")

    with pytest.raises(ContractError) as caught:
        compile_project(INCIDENT, build, check=True)

    assert caught.value.diagnostics[0].code == "COMPILE001"


def test_compile_rejects_output_inside_source_root(tmp_path: Path) -> None:
    (tmp_path / "project.contract").write_text("type Result:\n    ok: boolean\n")

    with pytest.raises(ContractError) as caught:
        compile_project(tmp_path, tmp_path / "types" / "build")

    assert caught.value.diagnostics[0].code == "COMPILE002"
