from __future__ import annotations

from pathlib import Path

import pytest

from contract4agents.ast import SourceSpan
from contract4agents.language_service import LanguageService, SourcePosition, SourceRange
from contract4agents.language_service._model import SourceSemanticToken
from contract4agents.parser import parse_source

TYPES = """\
type SectionResearchInput:
    topic: string
    max_sources: integer

type SectionResearchBrief:
    summary: string
    sources: list[string]

tool research.web_search(query: string) -> SectionResearchBrief:
    description = "Search the web."
    side_effect = false
"""

AGENT = """\
agent SectionResearchAgent(request: SectionResearchInput) -> SectionResearchBrief:
    use research.web_search:
        availability = enabled
        authorization = preapproved
        execution = provider_hosted
    goal = "Research one section."
"""


def test_parse_source_supports_unsaved_text() -> None:
    module = parse_source(Path("memory.contract"), "type Input:\n    topic: string")

    assert module.types[0].name == "Input"
    assert module.types[0].fields[0].type_name == "string"


def test_hover_explains_contract_values_and_renders_type_shapes(tmp_path: Path) -> None:
    types_path, agent_path, service = _project(tmp_path)

    enabled = service.hover(agent_path, _position_of(AGENT, "enabled"))
    input_type = service.hover(agent_path, _position_of(AGENT, "SectionResearchInput"))
    tool = service.hover(agent_path, _position_of(AGENT, "research.web_search"))

    assert enabled is not None and "permitted" in enabled
    assert input_type is not None and "max_sources: integer" in input_type
    assert tool is not None and "tool research.web_search(query: string)" in tool
    assert types_path in service.workspace_for(agent_path).documents


def test_definitions_references_and_rename_are_project_wide(tmp_path: Path) -> None:
    types_path, agent_path, service = _project(tmp_path)
    input_position = _position_of(AGENT, "SectionResearchInput")
    tool_position = _position_of(AGENT, "research.web_search")

    assert service.definition(agent_path, input_position) == [
        (types_path, _range_of(TYPES, "SectionResearchInput"))
    ]
    assert service.definition(agent_path, tool_position) == [
        (types_path, _range_of(TYPES, "research.web_search"))
    ]
    assert len(service.references(agent_path, input_position)) == 2

    edits = service.rename(agent_path, input_position, "ResearchRequest")
    assert {(edit.path, edit.new_text) for edit in edits} == {
        (types_path, "ResearchRequest"),
        (agent_path, "ResearchRequest"),
    }

    with pytest.raises(ValueError, match="not a valid type name"):
        service.rename(agent_path, input_position, "not-valid")


def test_completions_are_contextual_and_come_from_the_shared_spec(tmp_path: Path) -> None:
    _types_path, agent_path, service = _project(tmp_path)

    authorization = _completion_labels(service, agent_path, AGENT, "authorization = ")
    capability = _completion_labels(service, agent_path, AGENT, "use research.web")
    input_type = _completion_labels(service, agent_path, AGENT, "request: SectionResearchInput")

    assert authorization == {"preapproved", "approval_required"}
    assert "research.web_search" in capability
    assert {"SectionResearchInput", "SectionResearchBrief", "string", "list[]"} <= input_type


def test_invalid_edits_keep_the_last_valid_project_snapshot(tmp_path: Path) -> None:
    types_path, agent_path, service = _project(tmp_path)
    service.update_document(types_path, "type SectionResearchInput:\n    topic:")

    workspace = service.workspace_for(types_path)
    diagnostics = workspace.diagnostics()
    input_type = service.hover(agent_path, _position_of(AGENT, "SectionResearchInput"))
    input_definition = service.definition(agent_path, _position_of(AGENT, "SectionResearchInput"))

    assert diagnostics[types_path][0].code == "PARSE001"
    assert "SectionResearchInput" in workspace.project().types
    assert input_type is not None and "topic: string" in input_type
    assert input_definition[0][0] == types_path


def test_semantic_diagnostics_offer_conservative_grant_fixes(tmp_path: Path) -> None:
    (tmp_path / "types.contract").write_text(TYPES)
    broken = """\
agent SectionResearchAgent(request: SectionResearchInput) -> SectionResearchBrief:
    use research.web_search:
        availability = enabled
"""
    agent_path = tmp_path / "agent.contract"
    agent_path.write_text(broken)
    service = LanguageService()
    workspace = service.add_root(tmp_path)
    diagnostics = workspace.diagnostics()[agent_path.resolve()]

    authorization = next(item for item in diagnostics if item.code == "SEM107")
    execution = next(item for item in diagnostics if item.code == "SEM108")
    authorization_fixes = service.quick_fixes(agent_path, authorization)
    execution_fixes = service.quick_fixes(agent_path, execution)

    assert authorization.span == SourceSpan(agent_path.resolve(), 2, 9)
    assert authorization_fixes[0].preferred
    assert "authorization = approval_required" in authorization_fixes[0].edits[0].new_text
    assert authorization_fixes[0].edits[0].range.start == SourcePosition(3, 0)
    assert {fix.title for fix in execution_fixes} == {
        "Set execution to host",
        "Set execution to provider_hosted",
        "Set execution to remote",
    }


def test_run_specs_and_expressions_participate_in_navigation(tmp_path: Path) -> None:
    types_path, agent_path, service = _project(tmp_path)
    run_spec = """\
run_spec ResearchRun:
    stages = [section: SectionResearchAgent -> SectionResearchBrief]
    assertions = [expect(trace.agent_called(SectionResearchAgent))]

eval researches_section for SectionResearchAgent:
    given request = SectionResearchInput.fixture("default")
    expect output conforms SectionResearchBrief
"""
    run_path = tmp_path / "research.eval"
    run_path.write_text(run_spec)
    service.refresh_document(run_path)

    agent_definition = service.definition(
        run_path,
        _position_of(run_spec, "SectionResearchAgent", occurrence=1),
    )
    type_definition = service.definition(run_path, _position_of(run_spec, "SectionResearchInput"))

    assert agent_definition[0][0] == agent_path
    assert type_definition[0][0] == types_path
    declarations = service.document(run_path)
    assert declarations is not None
    assert [item.name for item in declarations.declarations] == ["ResearchRun", "researches_section"]


def test_tool_grants_have_conservative_inlay_hints(tmp_path: Path) -> None:
    _types_path, agent_path, service = _project(tmp_path)

    hints = service.inlay_hints(
        agent_path,
        _range_of(AGENT, "research.web_search"),
    )

    assert len(hints) == 1
    assert hints[0].label == "  (query: string) -> SectionResearchBrief"


def test_full_language_surface_is_indexed_and_rendered(tmp_path: Path) -> None:
    fixture_root = Path(__file__).parents[2] / "editors" / "vscode" / "test" / "fixtures"
    source = (fixture_root / "full.contract").read_text()
    eval_source = (fixture_root / "full.eval").read_text()
    contract_path = (tmp_path / "full.contract").resolve()
    eval_path = (tmp_path / "full.eval").resolve()
    contract_path.write_text(source)
    eval_path.write_text(eval_source)
    service = LanguageService()
    service.add_root(tmp_path)

    document = service.document(contract_path)
    assert document is not None
    assert {item.kind for item in document.declarations} == {
        "agent",
        "composition",
        "control",
        "datasource",
        "enum",
        "eval",
        "external_context",
        "isolation",
        "operational_control",
        "quality",
        "run_spec",
        "tool",
        "type",
    }

    hover_expectations = {
        "ResearchStatus": "Closed string enum",
        "topic": "Declared by `ResearchRequest`",
        "accounts.profile": "Resolve the account profile",
        "current_account": "Sensitivity: `confidential`",
        "EvidenceWorker": "Multidimensional isolation requirement",
        "EvidenceAnalyst": "agent EvidenceAnalyst",
        "investigate": "composition investigate",
        "evidence_required": "Assessment: `runtime`",
        "evidence_backed": "result is supported by evidence",
        "latency": "Host-observed operational requirement",
        "current_evidence": "2 expectation(s)",
        "ResearchRun": "4 stage(s)",
        "optional_review": "Declared by run spec `ResearchRun`",
    }
    for name, expected in hover_expectations.items():
        hover = service.hover(contract_path, _position_of(source, name))
        assert hover is not None and expected in hover

    eval_hover = service.hover(eval_path, _position_of(eval_source, "ResearchResult"))
    assert eval_hover is not None and "evidence_ids: list[string]" in eval_hover


def test_semantic_tokens_only_augment_meaningful_source(tmp_path: Path) -> None:
    _types_path, agent_path, service = _project(tmp_path)
    tokens = service.semantic_tokens(agent_path)

    assert _semantic_kind_at(tokens, AGENT, "SectionResearchAgent") == "class"
    assert _semantic_kind_at(tokens, AGENT, "SectionResearchInput") == "type"
    assert _semantic_kind_at(tokens, AGENT, "availability") == "property"
    assert _semantic_kind_at(tokens, AGENT, "enabled") == "enumMember"
    assert _semantic_kind_at(tokens, AGENT, "Research one section") is None


def test_nested_contract_projects_are_isolated_by_their_binding_file(tmp_path: Path) -> None:
    root_path = (tmp_path / "root.contract").resolve()
    root_path.write_text("type RootInput:\n    value: string\n")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "contract4agents.targets.toml").write_text("")
    nested_path = (nested / "nested.contract").resolve()
    nested_source = """\
type NestedInput:
    value: string

agent Worker(request: NestedInput) -> NestedInput:
    goal = "Keep the project isolated."
"""
    nested_path.write_text(nested_source)

    service = LanguageService()
    root_workspace = service.add_root(tmp_path)
    nested_workspace = service.workspace_for(nested_path)

    assert set(root_workspace.documents) == {root_path}
    assert set(nested_workspace.documents) == {nested_path}
    definition = service.definition(nested_path, _position_of(nested_source, "NestedInput", occurrence=1))
    assert definition[0][0] == nested_path
    assert all(not diagnostics for diagnostics in nested_workspace.diagnostics().values())


def _project(tmp_path: Path) -> tuple[Path, Path, LanguageService]:
    types_path = (tmp_path / "types.contract").resolve()
    agent_path = (tmp_path / "agent.contract").resolve()
    types_path.write_text(TYPES)
    agent_path.write_text(AGENT)
    service = LanguageService()
    service.add_root(tmp_path)
    return types_path, agent_path, service


def _position_of(source: str, needle: str, *, occurrence: int = 0) -> SourcePosition:
    offset = -1
    for _ in range(occurrence + 1):
        offset = source.index(needle, offset + 1)
    before = source[:offset]
    return SourcePosition(before.count("\n"), len(before.rsplit("\n", 1)[-1]))


def _range_of(source: str, needle: str) -> SourceRange:
    position = _position_of(source, needle)
    return SourceRange(position, SourcePosition(position.line, position.character + len(needle)))


def _completion_labels(
    service: LanguageService,
    path: Path,
    source: str,
    line_suffix: str,
) -> set[str]:
    line = next(index for index, value in enumerate(source.splitlines()) if line_suffix in value)
    character = source.splitlines()[line].index(line_suffix) + len(line_suffix)
    return {item.label for item in service.completions(path, SourcePosition(line, character))}


def _semantic_kind_at(tokens: tuple[SourceSemanticToken, ...], source: str, needle: str) -> str | None:
    position = _position_of(source, needle)
    return next((item.kind for item in tokens if item.range.contains(position)), None)
