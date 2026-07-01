from __future__ import annotations

from pathlib import Path

import pytest

from contract4agents.compiler import build_artifacts, compile_project
from contract4agents.diagnostics import ContractError
from contract4agents.parser import parse_file, parse_project
from contract4agents.schema import type_to_schema
from contract4agents.semantics import analyze_project

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "examples" / "incident-command"


def test_parse_incident_project() -> None:
    project = parse_project(FIXTURE)

    assert "IncidentCommander" in project.agents
    assert "IncidentBrief" in project.types
    assert project.evals[0].name == "discovers_checkout_cause"
    assert project.monitors[0].name == "status_update_requires_approval"


def test_parse_agent_fields_and_uses() -> None:
    module = parse_file(FIXTURE / "agents" / "incident_commander.contract")
    agent = module.agents[0]

    assert agent.name == "IncidentCommander"
    assert agent.parameters[0].name == "request"
    assert agent.return_type == "IncidentBrief"
    assert {use.name for use in agent.uses} >= {"LogInvestigator", "status_page.draft_update"}
    assert agent.uses[-1].permission == "requires_approval"
    assert agent.text_attr("description")
    assert agent.list_attr("guards")


def test_lark_module_parser_characterizes_supported_surface(tmp_path: Path) -> None:
    path = tmp_path / "surface.contract"
    path.write_text(
        """
# leading comments and blank lines are ignored
type Input:
    id: str = "x"
    score: int between 1 and 3

type Output:
    ok: bool

datasource InputSource:
    python = "pkg.module:resolve"
    requires = [Input]
    produces = Input
    render = "markdown"
    cache = "none"

agent MultiLine(
    input: Input
) -> Output:
    use datasource InputSource from ./datasources/input
    use tool tool.name from ./tools/name requires approval
    policy = ["a", "b"]
    assertions = [
        expect(output.ok == true),
        when(trace.tool_called(tool.name), expect(output.ok == true)),
    ]
    goal = "ok"

monitor approval_required for MultiLine:
    severity = "high"
    when trace.tool_called(tool.name)
    expect trace.approval_granted(tool.name)
""".strip()
    )

    module = parse_file(path)

    assert module.types[0].fields[0].default == '"x"'
    assert module.types[0].fields[1].type_name == "int between 1 and 3"
    assert module.datasources[0].requires == ["Input"]
    assert module.agents[0].parameters[0].name == "input"
    assert module.agents[0].uses[1].permission == "requires_approval"
    assert module.agents[0].list_attr("policy") == ["a", "b"]
    assert module.agents[0].list_attr("assertions") == [
        "expect(output.ok == true)",
        "when(trace.tool_called(tool.name), expect(output.ok == true))",
    ]
    assert module.monitors[0].severity == "high"
    assert module.monitors[0].span.line == 28


def test_parser_reports_invalid_syntax(tmp_path: Path) -> None:
    path = tmp_path / "bad.contract"
    path.write_text("agent Bad(\n")

    with pytest.raises(ContractError) as exc:
        parse_file(path)

    assert exc.value.diagnostics[0].code.startswith("PARSE")


def test_semantic_analyzer_accepts_fixture() -> None:
    project = parse_project(FIXTURE)
    result = analyze_project(project)

    assert result.ok, [diagnostic.format() for diagnostic in result.diagnostics]


def test_semantic_analyzer_rejects_missing_type(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type Result:
    ok: bool

agent BadAgent(
    missing: MissingType
) -> Result:
    goal = "bad"
""".strip()
    )
    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert any(diagnostic.code == "SEM002" for diagnostic in result.diagnostics)


def test_semantic_analyzer_rejects_invalid_output_field(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type Result:
    ok: bool

agent BadAgent() -> Result:
    goal = "bad"
    assertions = [
        expect(output.missing == true),
    ]
""".strip()
    )
    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert any(diagnostic.code == "SEM050" for diagnostic in result.diagnostics)


def test_semantic_analyzer_rejects_unknown_guard_type_and_tool(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type Result:
    ok: bool

agent BadAgent() -> Result:
    use tool tools.known from ./tools
    goal = "bad"
    guards = [
        require(output conforms MissingType),
        forbid(tool.tools.missing unless approved_by_human),
    ]
""".strip()
    )
    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert {diagnostic.code for diagnostic in result.diagnostics} >= {"SEM002", "SEM053"}


def test_semantic_analyzer_rejects_duplicate_top_level_declarations(tmp_path: Path) -> None:
    (tmp_path / "a.contract").write_text(
        """
type Result:
    ok: bool

agent Same() -> Result:
    goal = "first"
""".strip()
    )
    (tmp_path / "b.contract").write_text(
        """
type Result:
    message: str

agent Same() -> Result:
    goal = "second"
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert [diagnostic.code for diagnostic in result.diagnostics].count("SEM000") == 2


def test_json_schema_generation() -> None:
    project = parse_project(FIXTURE)
    schema = type_to_schema(project.types["IncidentBrief"])

    assert schema["title"] == "IncidentBrief"
    assert schema["properties"]["evidence"]["type"] == "array"
    assert "summary" in schema["required"]


def test_compile_project_artifacts(tmp_path: Path) -> None:
    artifacts = compile_project(FIXTURE, tmp_path / "build")

    assert "IncidentCommander" in artifacts["manifests"]
    assert (tmp_path / "build" / "schemas" / "IncidentBrief.json").exists()
    assert (tmp_path / "build" / "instructions" / "IncidentCommander.md").exists()
    assert (tmp_path / "build" / "guards" / "guard-plan.json").exists()
    assert any(item["kind"] == "approval_required_tool" for item in artifacts["guard_plan"])
    assert artifacts["adapter_capability_matrix"]["openai"]["tools"]["status"] == "partial"


def test_compile_check_detects_stale_artifacts(tmp_path: Path) -> None:
    compile_project(FIXTURE, tmp_path / "build")
    manifest = tmp_path / "build" / "manifests" / "IncidentCommander.json"
    manifest.write_text("{}\n")

    with pytest.raises(ContractError):
        compile_project(FIXTURE, tmp_path / "build", check=True)


def test_compile_check_detects_stale_guard_plan(tmp_path: Path) -> None:
    compile_project(FIXTURE, tmp_path / "build")
    guard_plan = tmp_path / "build" / "guards" / "guard-plan.json"
    guard_plan.write_text("[]\n")

    with pytest.raises(ContractError) as exc:
        compile_project(FIXTURE, tmp_path / "build", check=True)

    assert "guard-plan.json" in str(exc.value.diagnostics[0].hint)


def test_build_artifacts_generates_docs() -> None:
    project = parse_project(FIXTURE)
    artifacts = build_artifacts(project)

    assert "summary.md" in artifacts["docs"]
    assert "IncidentCommander" in artifacts["docs"]["summary.md"]
