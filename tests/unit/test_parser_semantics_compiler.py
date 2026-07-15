from __future__ import annotations

from pathlib import Path

import pytest

from contract4agents.compiler import build_artifacts, compile_project
from contract4agents.diagnostics import ContractError
from contract4agents.ir import build_canonical_ir, semantic_id
from contract4agents.parser import parse_file, parse_project
from contract4agents.semantics import analyze_project

ROOT = Path(__file__).resolve().parents[2]
INCIDENT = ROOT / "examples" / "incident-command"


def test_public_example_uses_only_v2_source_semantics() -> None:
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
    assert not hasattr(project, "monitors")


def test_v2_parser_builds_shared_capabilities_grants_context_and_assurance(tmp_path: Path) -> None:
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
    assert not hasattr(project.agents["Worker"], "uses")
    assert semantic_id("tool", "records.lookup") in ir.capabilities
    assert semantic_id("control", "Worker", "evidence_required") in ir.controls
    assert semantic_id("quality", "Worker", "evidence_backed") in ir.qualities


@pytest.mark.parametrize(
    "source",
    [
        'type Imported from python "app.models:Imported"\n',
        'datasource records.current:\n    python = "app.context:current"\n',
        "type Result:\n    ok: boolean\nagent A() -> Result:\n    use tool lookup from tools.lookup preapproved\n",
        "type Result:\n    ok: boolean\nagent A() -> Result:\n    use agent B from ./b\n",
        (
            "type Result:\n    ok: boolean\n"
            "agent A() -> Result:\n    use datasource records.current from ./context\n"
        ),
        "type Result:\n    ok: boolean\nagent A() -> Result:\n    use hosted_tool openai.web_search\n",
        (
            "type Result:\n    ok: boolean\nagent A() -> Result:\n    goal = \"ok\"\n"
            "monitor old for A:\n    when trace.called(A)\n    expect trace.called(A)\n"
        ),
    ],
)
def test_removed_v1_source_forms_are_syntax_errors(tmp_path: Path, source: str) -> None:
    path = tmp_path / "legacy.contract"
    path.write_text(source)

    with pytest.raises(ContractError) as caught:
        parse_file(path)

    assert caught.value.diagnostics[0].code == "PARSE001"


@pytest.mark.parametrize("attribute", ["policy", "success", "host_context", "composition", "guards", "assertions"])
def test_removed_v1_agent_attributes_are_semantic_errors(tmp_path: Path, attribute: str) -> None:
    value = "[Result]" if attribute == "host_context" else '["legacy"]'
    (tmp_path / "legacy.contract").write_text(
        f"type Result:\n    ok: boolean\n\nagent A() -> Result:\n    {attribute} = {value}\n"
    )

    result = analyze_project(parse_project(tmp_path))

    assert [(item.code, item.message) for item in result.diagnostics] == [
        ("SEM070", f"Unknown agent attribute `{attribute}` on `A`")
    ]


@pytest.mark.parametrize(
    ("grant_body", "expected_code"),
    [
        ("availability = available\nauthorization = preapproved\nexecution = host", "SEM105"),
        ("availability = enabled\nauthorization = requires approval\nexecution = host", "SEM107"),
        ("availability = enabled\nauthorization = preapproved\nexecution = sandboxed", "SEM108"),
    ],
)
def test_removed_permission_spellings_are_semantic_errors(
    tmp_path: Path,
    grant_body: str,
    expected_code: str,
) -> None:
    indented = "\n".join(f"        {line}" for line in grant_body.splitlines())
    (tmp_path / "legacy.contract").write_text(
        "type Result:\n    ok: boolean\n\n"
        "tool lookup() -> Result:\n    description = \"Look up.\"\n    side_effect = false\n\n"
        f"agent A() -> Result:\n    use lookup:\n{indented}\n    goal = \"Use lookup.\"\n"
    )

    result = analyze_project(parse_project(tmp_path))

    assert expected_code in {item.code for item in result.diagnostics}


@pytest.mark.parametrize("legacy_type", ["str", "int", "bool", "Result[]", "list[str]"])
def test_removed_type_aliases_are_semantic_errors(tmp_path: Path, legacy_type: str) -> None:
    (tmp_path / "legacy.contract").write_text(
        f"type Result:\n    value: string\n\ntype Wrapper:\n    value: {legacy_type}\n"
    )

    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert any(item.code == "SEM002" for item in result.diagnostics)


def test_compile_project_is_the_canonical_v2_compiler(tmp_path: Path) -> None:
    artifacts = compile_project(INCIDENT, tmp_path / "build")

    assert artifacts.ir.agents[semantic_id("agent", "IncidentCommander")].name == "IncidentCommander"
    assert artifacts.contract_digest.startswith("sha256:")
    assert "IncidentBrief" in artifacts.schemas
    assert (tmp_path / "build" / "ir" / "contract.json").is_file()
    assert (tmp_path / "build" / "schemas" / "IncidentBrief.json").is_file()
    assert (tmp_path / "build" / "generated" / "python" / "models.py").is_file()
    for removed in ("manifests", "monitors", "guards", "types", "adapters"):
        assert not (tmp_path / "build" / removed).exists()


def test_build_artifacts_accepts_canonical_ir_only() -> None:
    project = parse_project(INCIDENT)
    artifacts = build_artifacts(build_canonical_ir(project))

    assert "IncidentCommander" in artifacts.instructions
    assert "summary.md" in {str(path) for path in artifacts.docs}


def test_compile_check_detects_stale_v2_artifacts(tmp_path: Path) -> None:
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
