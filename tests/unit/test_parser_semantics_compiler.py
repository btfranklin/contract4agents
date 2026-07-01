from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract4agents.compiler import build_artifacts, compile_project
from contract4agents.diagnostics import ContractError
from contract4agents.parser import parse_file, parse_project
from contract4agents.schema import type_to_schema
from contract4agents.semantics import analyze_project

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "examples" / "incident-command"
PYDANTIC_FIXTURE = ROOT / "tests" / "fixtures" / "contract_projects" / "pydantic-model-interop"


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


def test_parse_python_type_binding() -> None:
    module = parse_file(PYDANTIC_FIXTURE / "models.contract")

    assert module.types[0].name == "ResearchPlan"
    assert module.types[0].source == "python"
    assert module.types[0].python_ref == "tests.fixtures.pydantic_models:ResearchPlanModel"
    assert module.types[0].fields == []


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
    use hosted_tool openai.web_search context_size "medium"
    policy = ["a", "b"]
    assertions = [
        expect(output.ok == true),
        when(trace.tool_called(tool.name), expect(output.ok == true)),
        expect(trace.hosted_tool_called(openai.web_search)),
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
    assert module.agents[0].uses[2].kind == "hosted_tool"
    assert module.agents[0].uses[2].config == {"context_size": "medium"}
    assert module.agents[0].list_attr("policy") == ["a", "b"]
    assert module.agents[0].list_attr("assertions") == [
        "expect(output.ok == true)",
        "when(trace.tool_called(tool.name), expect(output.ok == true))",
        "expect(trace.hosted_tool_called(openai.web_search))",
    ]
    assert module.monitors[0].severity == "high"
    assert module.monitors[0].span.line == 30


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


def test_semantic_analyzer_rejects_malformed_python_type_ref(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text('type Bad from python "not-a-ref"\n')

    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["PYD002"]


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


def test_semantic_analyzer_rejects_malformed_semantic_eval(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type Result:
    ok: bool

agent BadAgent() -> Result:
    goal = "bad"
""".strip()
    )
    (tmp_path / "bad.eval").write_text(
        """
eval bad_semantic for BadAgent:
    expect semantic(output, bad)
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert any(diagnostic.code == "SEM056" for diagnostic in result.diagnostics)


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


def test_semantic_analyzer_rejects_invalid_hosted_tools(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type Result:
    ok: bool

agent BadAgent() -> Result:
    use hosted_tool anthropic.web_search context_size "medium"
    use hosted_tool openai.file_search context_size "medium"
    use hosted_tool openai.web_search unknown "medium"
    use hosted_tool openai.web_search context_size "huge"
    use hosted_tool openai.web_search context_size "low" denied
    assertions = [
        expect(trace.hosted_tool_called(openai.missing)),
        expect(trace.tool_called(openai.web_search)),
    ]
    goal = "bad"
""".strip()
    )
    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert {diagnostic.code for diagnostic in result.diagnostics} >= {
        "SEM061",
        "SEM062",
        "SEM063",
        "SEM064",
        "SEM065",
        "SEM055",
        "SEM053",
    }


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


def test_pydantic_schema_generation_requires_explicit_imports(tmp_path: Path) -> None:
    with pytest.raises(ContractError) as exc:
        compile_project(PYDANTIC_FIXTURE, tmp_path / "build")

    assert exc.value.diagnostics[0].code == "PYD000"
    assert "--allow-python-imports" in str(exc.value.diagnostics[0].hint)


def test_pydantic_schema_generation_with_imports(tmp_path: Path) -> None:
    artifacts = compile_project(PYDANTIC_FIXTURE, tmp_path / "build", allow_python_imports=True)

    plan_schema = artifacts["schemas"]["ResearchPlan"]
    summary_manifest = artifacts["manifests"]["ResearchPlanner"]
    bindings = {item["type"]: item for item in artifacts["type_bindings"]}
    binding_json = json.loads((tmp_path / "build" / "types" / "type-bindings.json").read_text())

    assert plan_schema["title"] == "ResearchPlan"
    assert plan_schema["properties"]["mode"]["default"] == "quick"
    assert plan_schema["$defs"]["ResearchSourceModel"]["properties"]["confidence"]["maximum"] == 1.0
    assert summary_manifest["inputs"][0]["python_ref"] == "tests.fixtures.pydantic_models:ResearchPlanModel"
    assert summary_manifest["output"]["python_ref"] == "tests.fixtures.pydantic_models:ResearchSummaryModel"
    assert bindings["ResearchPlan"]["source"] == "python"
    assert len(bindings["ResearchPlan"]["schema_hash"]) == 64
    assert binding_json[0]["schema_ref"].startswith("schemas/")


def test_pydantic_compile_check_detects_stale_type_bindings(tmp_path: Path) -> None:
    compile_project(PYDANTIC_FIXTURE, tmp_path / "build", allow_python_imports=True)
    (tmp_path / "build" / "types" / "type-bindings.json").write_text("[]\n")

    with pytest.raises(ContractError) as exc:
        compile_project(PYDANTIC_FIXTURE, tmp_path / "build", check=True, allow_python_imports=True)

    assert "type-bindings.json" in str(exc.value.diagnostics[0].hint)


@pytest.mark.parametrize(
    ("python_ref", "code"),
    [
        ("tests.fixtures.pydantic_models:MissingModel", "PYD010"),
        ("tests.fixtures.pydantic_models:NotPydantic", "PYD011"),
        ("tests.fixtures.pydantic_models:RootListModel", "PYD015"),
    ],
)
def test_pydantic_schema_generation_reports_bad_imports(tmp_path: Path, python_ref: str, code: str) -> None:
    (tmp_path / "bad.contract").write_text(
        f"""
type Bad from python "{python_ref}"

agent BadAgent() -> Bad:
    goal = "bad"
""".strip()
    )

    with pytest.raises(ContractError) as exc:
        compile_project(tmp_path, tmp_path / "build", allow_python_imports=True)

    assert exc.value.diagnostics[0].code == code


def test_compile_project_artifacts(tmp_path: Path) -> None:
    artifacts = compile_project(FIXTURE, tmp_path / "build")

    assert "IncidentCommander" in artifacts["manifests"]
    assert artifacts["manifests"]["IncidentCommander"]["source_path"].endswith("agents/incident_commander.contract")
    assert (tmp_path / "build" / "schemas" / "IncidentBrief.json").exists()
    assert (tmp_path / "build" / "instructions" / "IncidentCommander.md").exists()
    assert (tmp_path / "build" / "guards" / "guard-plan.json").exists()
    assert any(item["kind"] == "approval_required_tool" for item in artifacts["guard_plan"])
    assert artifacts["adapter_capability_matrix"]["openai"]["tools"]["status"] == "partial"
    assert artifacts["adapter_capability_matrix"]["openai"]["hosted_tools"]["status"] == "partial"
    assert artifacts["type_bindings"][0]["source"] == "native"


def test_compile_project_artifacts_include_hosted_tools(tmp_path: Path) -> None:
    project_dir = tmp_path / "hosted"
    project_dir.mkdir()
    (project_dir / "hosted.contract").write_text(
        """
type Result:
    ok: bool

agent HostedAgent() -> Result:
    use hosted_tool openai.web_search context_size "high" preapproved
    assertions = [
        expect(trace.hosted_tool_called(openai.web_search)),
    ]
    goal = "ok"
""".strip()
    )

    artifacts = compile_project(project_dir, tmp_path / "build")

    hosted_tool = artifacts["manifests"]["HostedAgent"]["hosted_tools"][0]
    assert hosted_tool == {
        "name": "openai.web_search",
        "provider": "openai",
        "tool": "web_search",
        "config": {"context_size": "high"},
        "permission": "preapproved",
    }
    assert "`HostedAgent` may use `openai.web_search` (context_size=high)" in artifacts["docs"]["summary.md"]


def test_compile_check_detects_stale_hosted_tool_manifest(tmp_path: Path) -> None:
    project_dir = tmp_path / "hosted"
    project_dir.mkdir()
    (project_dir / "hosted.contract").write_text(
        """
type Result:
    ok: bool

agent HostedAgent() -> Result:
    use hosted_tool openai.web_search context_size "high"
    goal = "ok"
""".strip()
    )
    compile_project(project_dir, tmp_path / "build")
    manifest = tmp_path / "build" / "manifests" / "HostedAgent.json"
    manifest.write_text("{}\n")

    with pytest.raises(ContractError) as exc:
        compile_project(project_dir, tmp_path / "build", check=True)

    assert "HostedAgent.json" in str(exc.value.diagnostics[0].hint)


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
