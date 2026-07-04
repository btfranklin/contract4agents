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
RUN_CONTRACT_FIXTURE = ROOT / "tests" / "fixtures" / "contract_projects" / "run-contracts"


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


def test_parse_run_contract_declaration() -> None:
    module = parse_file(RUN_CONTRACT_FIXTURE / "agents" / "research.contract")

    run_contract = module.run_contracts[0]

    assert run_contract.name == "CompendiumResearch"
    assert run_contract.stages == [
        "plan: PlannerAgent -> ResearchPlan",
        "section_research+: SectionResearchAgent -> SectionResearchBrief",
        "verification?: VerifierAgent -> VerificationReport",
        "synthesis: SynthesisAgent -> CompendiumPayload",
    ]
    assert run_contract.assertions[-1] == "expect(trace.not_tool_called_by(SynthesisAgent, openai.web_search))"


def test_run_contract_fixture_is_semantically_valid() -> None:
    result = analyze_project(parse_project(RUN_CONTRACT_FIXTURE))

    assert result.ok, [diagnostic.format() for diagnostic in result.diagnostics]


@pytest.mark.parametrize(
    ("body", "expected_codes"),
    [
        (
            """
run_contract BadRun:
    stages = [
        missing: MissingAgent -> MissingType,
    ]
""",
            {"SEM084", "SEM085"},
        ),
        (
            """
run_contract BadRun:
    stages = [
        duplicate: KnownAgent -> Result,
        duplicate: KnownAgent -> Result,
        malformed(KnownAgent),
    ]
""",
            {"SEM083", "SEM086"},
        ),
        (
            """
run_contract BadRun:
    stages = [
        known: KnownAgent -> Result,
    ]
    branches = []
    assertions = [
        expect(output.ok == true),
    ]
""",
            {"SEM080", "SEM087"},
        ),
        (
            """
run_contract BadRun:
    stages = [
        known: KnownAgent -> Result,
    ]
    assertions = [
        expect(trace.agent_called(OtherAgent)),
        expect(trace.not_tool_called_by(KnownAgent, missing.tool)),
    ]
""",
            {"SEM051", "SEM053"},
        ),
    ],
)
def test_run_contract_semantic_diagnostics(
    tmp_path: Path,
    body: str,
    expected_codes: set[str],
) -> None:
    (tmp_path / "bad.contract").write_text(
        f"""
type Result:
    ok: bool

agent KnownAgent() -> Result:
    goal = "known"

{body}
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert {diagnostic.code for diagnostic in result.diagnostics} >= expected_codes


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


@pytest.mark.parametrize(
    ("assignment", "code", "message"),
    [
        ("guard = [require(output conforms Result)]", "SEM070", "Unknown agent attribute `guard`"),
        ('guards = "require(output conforms Result)"', "SEM071", "Agent attribute `guards`"),
        ('assertions = "expect(output.ok == true)"', "SEM071", "Agent attribute `assertions`"),
        ('goal = ["bad"]', "SEM071", "Agent attribute `goal`"),
    ],
)
def test_compile_rejects_invalid_agent_attribute_assignments(
    tmp_path: Path,
    assignment: str,
    code: str,
    message: str,
) -> None:
    (tmp_path / "bad.contract").write_text(
        f"""
type Result:
    ok: bool

agent BadAgent() -> Result:
    {assignment}
""".strip()
    )

    with pytest.raises(ContractError) as exc:
        compile_project(tmp_path, tmp_path / "build")

    diagnostics = exc.value.diagnostics
    assert diagnostics[0].code == code
    assert message in diagnostics[0].message
    assert not (tmp_path / "build" / "guards" / "guard-plan.json").exists()


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
    assert next(diagnostic for diagnostic in result.diagnostics if diagnostic.code == "SEM061").severity == "warning"


def test_semantic_analyzer_allows_unknown_hosted_provider_with_warning(tmp_path: Path) -> None:
    (tmp_path / "unknown.contract").write_text(
        """
type Result:
    ok: bool

agent UnknownProviderAgent() -> Result:
    use hosted_tool example.search context_size "medium"
    assertions = [
        expect(trace.hosted_tool_called(example.search)),
    ]
    goal = "unknown provider"
""".strip()
    )
    result = analyze_project(parse_project(tmp_path))

    assert result.ok
    assert [(diagnostic.code, diagnostic.severity) for diagnostic in result.diagnostics] == [("SEM061", "warning")]


def test_eval_and_monitor_trace_refs_allow_child_dependency_capabilities(tmp_path: Path) -> None:
    _write_reachability_project(tmp_path)
    (tmp_path / "good.eval").write_text(
        """
eval good_child_refs for Parent:
    given start = "case"
    expect trace.agent_called(Child)
    expect trace.tool_called(child.lookup)
    expect trace.hosted_tool_called(openai.web_search)
    expect trace.datasource_resolved(ChildContext)
""".strip()
    )
    (tmp_path / "good.monitors.contract").write_text(
        """
monitor child_tool_required for Parent:
    severity = "high"
    when trace.agent_called(Child)
    expect trace.tool_called(child.lookup)
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert result.ok, [diagnostic.format() for diagnostic in result.diagnostics]


def test_eval_and_monitor_trace_refs_reject_unrelated_project_capabilities(tmp_path: Path) -> None:
    _write_reachability_project(tmp_path)
    (tmp_path / "bad.eval").write_text(
        """
eval bad_other_tool for Parent:
    given start = "case"
    expect trace.tool_called(other.lookup)
""".strip()
    )
    (tmp_path / "bad.monitors.contract").write_text(
        """
monitor other_agent_tool for Parent:
    severity = "high"
    when trace.agent_called(Other)
    expect trace.tool_called(other.lookup)
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert {diagnostic.code for diagnostic in result.diagnostics} >= {"SEM051", "SEM053"}


def test_semantic_analyzer_rejects_malformed_and_unknown_composition(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type Result:
    ok: bool

agent Parent() -> Result:
    use agent B from ./b
    composition = [handof(B), handoff(Missing)]
    goal = "bad"

agent B() -> Result:
    goal = "child"
""".strip()
    )
    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert [(diagnostic.code, diagnostic.message) for diagnostic in result.diagnostics] == [
        ("SEM066", "Malformed composition declaration `handof(B)` on agent `Parent`"),
        ("SEM067", "Composition declaration `handoff(Missing)` references unknown agent `Missing`"),
    ]


def test_semantic_analyzer_requires_composition_use_agent_dependency(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type Result:
    ok: bool

agent Parent() -> Result:
    composition = [agent_as_tool(Child)]
    goal = "bad"

agent Child() -> Result:
    goal = "child"
""".strip()
    )
    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert [(diagnostic.code, diagnostic.message) for diagnostic in result.diagnostics] == [
        (
            "SEM068",
            "Composition declaration `agent_as_tool(Child)` references agent `Child` "
            "without a matching `use agent` dependency",
        )
    ]


def test_semantic_analyzer_accepts_child_context_from_parent_inputs(tmp_path: Path) -> None:
    (tmp_path / "good.contract").write_text(
        """
type SharedContext:
    value: str

type Result:
    ok: bool

agent Parent(shared: SharedContext) -> Result:
    use agent Child from ./child
    goal = "parent"

agent Child(shared: SharedContext) -> Result:
    goal = "child"
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert result.ok, [diagnostic.format() for diagnostic in result.diagnostics]


def test_semantic_analyzer_rejects_missing_child_context(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type Request:
    value: str

type ChildContext:
    value: str

type Result:
    ok: bool

agent Parent(request: Request) -> Result:
    use agent Child from ./child
    goal = "parent"

agent Child(context: ChildContext) -> Result:
    goal = "child"
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    diagnostic = next(item for item in result.diagnostics if item.code == "SEM072")
    assert "Parent" in diagnostic.message
    assert "ChildContext" in diagnostic.message
    assert "Child" in diagnostic.message


def test_semantic_analyzer_treats_optional_parent_context_as_not_required_context(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type ChildContext:
    value: str

type Result:
    ok: bool

agent Parent(context: ChildContext?) -> Result:
    use agent Child from ./child
    goal = "parent"

agent Child(context: ChildContext) -> Result:
    goal = "child"
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert any(item.code == "SEM072" for item in result.diagnostics)


def test_semantic_analyzer_accepts_child_context_from_parent_datasource(tmp_path: Path) -> None:
    (tmp_path / "good.contract").write_text(
        """
type Request:
    value: str

type ChildContext:
    value: str

type Result:
    ok: bool

datasource ChildContextSource:
    python = "pkg.context:load"
    requires = [Request]
    produces = ChildContext

agent Parent(request: Request) -> Result:
    use datasource ChildContextSource from ./context
    use agent Child from ./child
    goal = "parent"

agent Child(context: ChildContext) -> Result:
    goal = "child"
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert result.ok, [diagnostic.format() for diagnostic in result.diagnostics]


def test_semantic_analyzer_accepts_child_context_from_recursive_datasource_chain(tmp_path: Path) -> None:
    (tmp_path / "good.contract").write_text(
        """
type Request:
    value: str

type Intermediate:
    value: str

type ChildContext:
    value: str

type Result:
    ok: bool

datasource IntermediateSource:
    python = "pkg.context:intermediate"
    requires = [Request]
    produces = Intermediate

datasource ChildContextSource:
    python = "pkg.context:child"
    requires = [Intermediate]
    produces = ChildContext

agent Parent(request: Request) -> Result:
    use datasource IntermediateSource from ./context
    use datasource ChildContextSource from ./context
    use agent Child from ./child
    goal = "parent"

agent Child(context: ChildContext) -> Result:
    goal = "child"
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert result.ok, [diagnostic.format() for diagnostic in result.diagnostics]


def test_semantic_analyzer_rejects_ambiguous_child_context_datasource(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type Request:
    value: str

type ChildContext:
    value: str

type Result:
    ok: bool

datasource ChildContextSourceA:
    python = "pkg.context:a"
    requires = [Request]
    produces = ChildContext

datasource ChildContextSourceB:
    python = "pkg.context:b"
    requires = [Request]
    produces = ChildContext

agent Parent(request: Request) -> Result:
    use datasource ChildContextSourceA from ./context
    use datasource ChildContextSourceB from ./context
    use agent Child from ./child
    goal = "parent"

agent Child(context: ChildContext) -> Result:
    goal = "child"
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert {item.code for item in result.diagnostics} >= {"SEM022", "SEM073"}


def test_semantic_analyzer_rejects_child_context_datasource_cycle(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type ChildContext:
    value: str

type OtherContext:
    value: str

type Result:
    ok: bool

datasource ChildContextSource:
    python = "pkg.context:child"
    requires = [OtherContext]
    produces = ChildContext

datasource OtherContextSource:
    python = "pkg.context:other"
    requires = [ChildContext]
    produces = OtherContext

agent Parent() -> Result:
    use datasource ChildContextSource from ./context
    use datasource OtherContextSource from ./context
    use agent Child from ./child
    goal = "parent"

agent Child(context: ChildContext) -> Result:
    goal = "child"
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    diagnostic = next(item for item in result.diagnostics if item.code == "SEM074")
    assert "ChildContext -> OtherContext -> ChildContext" in str(diagnostic.hint)


def test_semantic_analyzer_accepts_explicit_host_context(tmp_path: Path) -> None:
    (tmp_path / "good.contract").write_text(
        """
type Request:
    value: str

type ChildContext:
    value: str

type Result:
    ok: bool

agent Parent(request: Request) -> Result:
    use agent Child from ./child
    host_context = [ChildContext]
    goal = "parent"

agent Child(context: ChildContext) -> Result:
    goal = "child"
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert result.ok, [diagnostic.format() for diagnostic in result.diagnostics]


def test_semantic_analyzer_rejects_unknown_host_context_type(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type Result:
    ok: bool

agent Parent() -> Result:
    host_context = [MissingContext]
    goal = "parent"
""".strip()
    )

    result = analyze_project(parse_project(tmp_path))

    assert not result.ok
    assert any(item.code == "SEM002" and "MissingContext" in item.message for item in result.diagnostics)


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


def test_compile_project_refuses_source_root_output_and_preserves_docs(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    source_doc = tmp_path / "docs" / "source.md"
    source_doc.write_text("source docs\n")
    (tmp_path / "project.contract").write_text(
        """
type Result:
    ok: bool

agent RootAgent() -> Result:
    goal = "ok"
""".strip()
    )

    with pytest.raises(ContractError) as exc:
        compile_project(tmp_path, tmp_path)

    assert exc.value.diagnostics[0].code == "COMPILE002"
    assert source_doc.read_text() == "source docs\n"

    with pytest.raises(ContractError) as docs_exc:
        compile_project(tmp_path, tmp_path / "docs")

    assert docs_exc.value.diagnostics[0].code == "COMPILE002"
    assert source_doc.read_text() == "source docs\n"


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
    assert artifacts["manifests"]["IncidentCommander"]["host_context"] == [
        {"type": "IncidentBrief", "python_ref": None}
    ]
    assert (tmp_path / "build" / "schemas" / "IncidentBrief.json").exists()
    assert (tmp_path / "build" / "instructions" / "IncidentCommander.md").exists()
    assert (tmp_path / "build" / "docs" / "agents" / "IncidentCommander.md").exists()
    assert (tmp_path / "build" / "guards" / "guard-plan.json").exists()
    assert any(item["kind"] == "approval_required_tool" for item in artifacts["guard_plan"])
    assert artifacts["adapter_capability_matrix"]["openai"]["tools"]["status"] == "partial"
    assert artifacts["adapter_capability_matrix"]["openai"]["hosted_tools"]["status"] == "partial"
    assert artifacts["adapter_capability_matrix"]["openai"]["isolated_subagent"]["status"] == "unsupported"
    assert artifacts["type_bindings"][0]["source"] == "native"


def test_compile_project_artifacts_include_run_contracts(tmp_path: Path) -> None:
    artifacts = compile_project(RUN_CONTRACT_FIXTURE, tmp_path / "build")

    run_contract = artifacts["run_contracts"][0]

    assert run_contract["name"] == "CompendiumResearch"
    assert run_contract["stages"][1] == {
        "name": "section_research",
        "agent": "SectionResearchAgent",
        "output_type": "SectionResearchBrief",
        "cardinality": "many",
        "manifest_ref": "manifests/SectionResearchAgent.json",
        "schema_ref": "schemas/SectionResearchBrief.json",
    }
    assert (tmp_path / "build" / "run-contracts" / "run-contracts.json").exists()
    assert "`CompendiumResearch`" in artifacts["docs"]["summary.md"]


def test_compile_check_detects_stale_run_contract_artifact(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    compile_project(RUN_CONTRACT_FIXTURE, build_dir)
    run_contracts = build_dir / "run-contracts" / "run-contracts.json"
    run_contracts.write_text("[]\n")

    with pytest.raises(ContractError) as exc:
        compile_project(RUN_CONTRACT_FIXTURE, build_dir, check=True)

    assert "run-contracts.json" in str(exc.value.diagnostics[0].hint)


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


def test_compile_deletes_unexpected_managed_artifacts_but_keeps_visualization(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    compile_project(FIXTURE, build_dir)
    stale_manifest = build_dir / "manifests" / "RemovedAgent.json"
    stale_manifest.write_text("{}\n")
    visualization_output = build_dir / "visualization" / "index.html"
    visualization_output.parent.mkdir(parents=True)
    visualization_output.write_text("<!doctype html>\n")

    compile_project(FIXTURE, build_dir)

    assert not stale_manifest.exists()
    assert visualization_output.exists()


def test_compile_check_detects_unexpected_managed_file(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    compile_project(FIXTURE, build_dir)
    extra_doc = build_dir / "docs" / "old-summary.md"
    extra_doc.write_text("stale\n")

    with pytest.raises(ContractError) as exc:
        compile_project(FIXTURE, build_dir, check=True)

    assert "old-summary.md" in str(exc.value.diagnostics[0].hint)


def test_build_artifacts_generates_docs() -> None:
    project = parse_project(FIXTURE)
    artifacts = build_artifacts(project)

    assert "summary.md" in artifacts["docs"]
    assert "agents/IncidentCommander.md" in artifacts["docs"]
    assert "IncidentCommander" in artifacts["docs"]["summary.md"]
    agent_doc = artifacts["docs"]["agents/IncidentCommander.md"]
    assert "| Name | Source | Permission |" in agent_doc
    assert "| status_page.draft_update | tools.status_page | requires_approval |" in agent_doc
    assert "| IncidentBrief |  |" in agent_doc
    assert "- `discovers_checkout_cause`" in agent_doc
    assert "Output schema: `schemas/IncidentBrief.json`" in agent_doc


def _write_reachability_project(path: Path) -> None:
    (path / "project.contract").write_text(
        """
type ChildContext:
    value: str

type Result:
    ok: bool

datasource ChildContextSource:
    python = "pkg.context:load"
    produces = ChildContext
    requires = []

agent Parent() -> Result:
    use agent Child from ./child
    goal = "parent"

agent Child() -> Result:
    use tool child.lookup from ./tools
    use hosted_tool openai.web_search context_size "medium"
    use datasource ChildContextSource from ./context
    goal = "child"

agent Other() -> Result:
    use tool other.lookup from ./tools
    goal = "other"
""".strip()
    )
