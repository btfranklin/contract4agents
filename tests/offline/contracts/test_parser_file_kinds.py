from pathlib import Path

import pytest

from contract4agents.compiler import compile_project
from contract4agents.diagnostics import ContractError
from contract4agents.ir import canonical_ir_json
from contract4agents.language_service._source import parse_document
from contract4agents.parser import parse_file, parse_project, parse_source

DECLARATIONS = {
    "types": 'type T:\n    value: string\n',
    "enums": 'enum E:\n    "one"\n',
    "tools": 'tool t() -> string:\n    description = "search"\n',
    "datasources": 'datasource d() -> string:\n    description = "data"\n',
    "external_contexts": 'external_context c -> string:\n    description = "context"\n',
    "isolations": 'isolation i:\n    context = explicit_only\n',
    "compositions": 'composition c from A to B:\n    mode = delegate\n',
    "controls": 'control c for A:\n    assessment = post_run\n    require = trace.agent_called(B)\n',
    "qualities": 'quality q for A:\n    rubric = "The result is clear."\n',
    "operational_controls": 'operational_control o for A:\n    require = trace.duration < 10s\n',
    "agents": 'agent A() -> string:\n    goal = "Do work."\n',
    "run_specs": 'run_spec R:\n    stages = []\n',
    "evals": 'eval e for A:\n    expect output == 1\n',
}
ALLOWED = {
    ".agent.contract": {"agents"},
    ".types.contract": {"types", "enums"},
    ".capabilities.contract": {"tools", "datasources", "external_contexts"},
    ".composition.contract": {"compositions"},
    ".assurance.contract": {"controls", "qualities", "operational_controls"},
    ".run.contract": {"run_specs"},
    ".eval": {"evals"},
}


@pytest.mark.parametrize("suffix,allowed", ALLOWED.items())
@pytest.mark.parametrize("kind,source", DECLARATIONS.items())
def test_each_specialized_suffix_accepts_only_its_declaration_kinds(
    suffix: str, allowed: set[str], kind: str, source: str
) -> None:
    path = Path("sample" + suffix)
    if kind in allowed:
        parse_source(path, source)
    else:
        with pytest.raises(ContractError) as caught:
            parse_source(path, source)
        assert caught.value.diagnostics[0].code == "PARSE003"
        assert caught.value.diagnostics[0].span is not None


@pytest.mark.parametrize("suffix", ALLOWED)
@pytest.mark.parametrize("source", ["", "# No declarations.\n"])
def test_specialized_files_reject_empty_content(suffix: str, source: str) -> None:
    with pytest.raises(ContractError) as caught:
        parse_source(Path("empty" + suffix), source)
    assert caught.value.diagnostics[0].code == "PARSE003"


@pytest.mark.parametrize("suffix,allowed", ALLOWED.items())
def test_specialized_files_reject_other_kinds_mixed_with_allowed_content(
    suffix: str, allowed: set[str]
) -> None:
    accepted = "\n".join(source for kind, source in DECLARATIONS.items() if kind in allowed)
    parse_source(Path("valid" + suffix), accepted)
    for kind, source in DECLARATIONS.items():
        if kind not in allowed:
            with pytest.raises(ContractError) as caught:
                parse_source(Path("mixed" + suffix), accepted + "\n" + source)
            assert caught.value.diagnostics[0].code == "PARSE003"


def test_agent_file_allows_nested_statements_and_requires_exactly_one_agent() -> None:
    source = '''agent A(request: Request) -> Reply:
    use tools.search:
        availability = enabled
        authorization = preapproved
        execution = host
    context record: Record from external current_record
    goal = "Do work."
    guidance = ["Use evidence."]
'''
    parse_source(Path("a.agent.contract"), source)

    invalid_sources = ("", source + "\n" + source.replace("agent A", "agent B"), source + "\n" + DECLARATIONS["types"])
    for invalid in invalid_sources:
        with pytest.raises(ContractError, match="exactly one agent and no other top-level declarations"):
            parse_source(Path("a.agent.contract"), invalid)


def test_generic_files_keep_mixed_and_empty_content() -> None:
    parse_source(Path("mixed.contract"), DECLARATIONS["agents"] + DECLARATIONS["types"])
    parse_source(Path("empty.contract"), "")
    parse_source(Path("agent.contract"), DECLARATIONS["types"])


def test_parse_file_and_project_reject_mismatched_suffix(tmp_path: Path) -> None:
    path = tmp_path / "wrong.agent.contract"
    path.write_text(DECLARATIONS["types"])

    for parse in (lambda: parse_file(path), lambda: parse_project(tmp_path)):
        with pytest.raises(ContractError) as caught:
            parse()
        assert caught.value.diagnostics[0].code == "PARSE003"


def test_editor_document_returns_suffix_diagnostic() -> None:
    document = parse_document(Path("wrong.types.contract"), DECLARATIONS["agents"])

    assert document.module is None
    assert document.diagnostics[0].code == "PARSE003"


def test_split_project_compiles_like_combined_generic_source(tmp_path: Path) -> None:
    agent = '''agent A(request: Request) -> Reply:
    goal = "Use the request."
'''
    types = '''type Request:
    topic: string
type Reply:
    summary: string
'''
    split = tmp_path / "split"
    split.mkdir()
    (split / "agent.agent.contract").write_text(agent)
    (split / "types.types.contract").write_text(types)
    combined = tmp_path / "combined"
    combined.mkdir()
    (combined / "combined.contract").write_text(agent + "\n" + types)

    split_artifacts = compile_project(split)
    combined_artifacts = compile_project(combined)
    assert canonical_ir_json(split_artifacts.ir) == canonical_ir_json(combined_artifacts.ir)
    assert split_artifacts.contract_digest == combined_artifacts.contract_digest
    assert split_artifacts.instructions == combined_artifacts.instructions
