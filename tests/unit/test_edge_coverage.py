from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from contract4agents.adapters.openai import OpenAIAdapterUnavailable, build_openai_agent
from contract4agents.ast import FieldDef, SourceSpan, TypeDef
from contract4agents.cli import main
from contract4agents.diagnostics import ContractError, Diagnostic, raise_if_errors
from contract4agents.docscheck import check_docs
from contract4agents.evaluation import EvalRunner
from contract4agents.monitor import MonitorRule, run_monitors
from contract4agents.parser import parse_file, parse_project
from contract4agents.runtime import (
    ContextValue,
    DatasourceContext,
    DatasourceExecutionFailed,
    DatasourcePermissionDenied,
    DatasourceRegistry,
    DatasourceResolutionCycle,
    DatasourceSpec,
    FakeToolRegistry,
    MissingContextSlot,
    RuntimeContext,
    ToolExecutionFailed,
    ToolPermissionDenied,
    load_python_ref,
    run_async,
)
from contract4agents.schema import type_to_schema
from contract4agents.semantics import analyze_project


def test_source_span_display_and_raise_if_errors(tmp_path: Path) -> None:
    diagnostic = Diagnostic("X", "broken", span=SourceSpan(tmp_path / "a.contract", 2, 3))

    assert "a.contract:2:3" in diagnostic.format()
    with pytest.raises(ContractError):
        raise_if_errors([diagnostic])


def test_schema_variants(tmp_path: Path) -> None:
    type_def = TypeDef(
        "Variant",
        [
            FieldDef("nullable", "str", nullable=True),
            FieldDef("default_null", "str", default="null"),
            FieldDef("default_bool", "bool", default="true"),
            FieldDef("default_float", "float", default="1.5"),
            FieldDef("literal", '"a" | "b"'),
            FieldDef("bounded", "int between 1 and 3"),
            FieldDef("ref", "Other"),
            FieldDef("listy", "list[str]"),
        ],
        SourceSpan(tmp_path / "types.contract", 1),
    )
    schema = type_to_schema(type_def)

    assert schema["properties"]["nullable"]["anyOf"][1]["type"] == "null"
    assert schema["properties"]["default_null"]["default"] is None
    assert schema["properties"]["default_bool"]["default"] is True
    assert schema["properties"]["default_float"]["default"] == 1.5
    assert schema["properties"]["literal"]["enum"] == ["a", "b"]
    assert schema["properties"]["bounded"]["maximum"] == 3.0
    assert schema["properties"]["ref"]["$ref"] == "#/$defs/Other"
    assert schema["properties"]["listy"]["items"]["type"] == "string"


def test_parser_datasource_and_error_edges(tmp_path: Path) -> None:
    good = tmp_path / "good.contract"
    good.write_text(
        """
type Input:
    id: str = "x"

type Output:
    ok: bool

datasource InputSource:
    python = "pkg.module:resolve"
    requires = [Input]
    produces = Input
    render = "markdown"
    cache = "none"

agent OneLine(input: Input) -> Output:
    use datasource InputSource from ./datasources/input
    policy = ["a", "b"]
    goal = "ok"
""".strip()
    )
    module = parse_file(good)
    assert module.datasources[0].cache == "none"
    assert module.agents[0].list_attr("policy") == ["a", "b"]

    bads = {
        "unknown.contract": "wat",
        "bad_type.contract": "type Bad",
        "bad_ds.contract": "datasource D:\n    produces = Missing",
        "bad_agent.contract": "agent Bad -> Missing:",
        "bad_eval.eval": "eval bad Bad:",
        "bad_monitor.contract": "monitor bad Bad:",
        "bad_use.contract": "type O:\n    ok: bool\nagent A() -> O:\n    use tool x from y weird",
        "bad_list.contract": "type O:\n    ok: bool\nagent A() -> O:\n    policy = [",
    }
    for name, text in bads.items():
        path = tmp_path / name
        path.write_text(text)
        with pytest.raises(ContractError):
            parse_file(path)


def test_semantic_error_edges(tmp_path: Path) -> None:
    (tmp_path / "bad.contract").write_text(
        """
type T:
    same: str
    same: str

type O:
    ok: bool

datasource BadSource:
    python = "badref"
    requires = [Unknown]
    produces = T
    cache = "forever"

agent A(input: T) -> O:
    use agent MissingAgent from ./missing
    use datasource MissingSource from ./missing
    assertions = [
        expect(trace.called(MissingAgent)),
    ]

monitor bad for MissingAgent:
    when trace.tool_called(x)
    expect trace.contains("x")
""".strip()
    )
    result = analyze_project(parse_project(tmp_path))
    codes = {diagnostic.code for diagnostic in result.diagnostics}

    assert {"SEM001", "SEM002", "SEM010", "SEM011", "SEM020", "SEM021", "SEM051", "SEM030"} <= codes


@pytest.mark.asyncio
async def test_runtime_edges(tmp_path: Path) -> None:
    trace = RuntimeContext(
        {
            "Visible": ContextValue("Visible", "v", "visible", "test"),
            "Secret": ContextValue("Secret", "s", "secret", "test", sensitive=True),
        }
    )
    assert "visible" in trace.rendered_context()
    assert "secret" not in trace.rendered_context()

    def raw(_ctx: DatasourceContext) -> str:
        return "raw"

    registry = DatasourceRegistry()
    registry.register("NoRenderer", DatasourceSpec("NoRenderer", "Raw", [], raw))
    with pytest.raises(DatasourceExecutionFailed):
        await trace.resolve_one("Raw", registry)
    assert trace.trace.count("datasource.failed") == 1

    with pytest.raises(DatasourcePermissionDenied):
        await RuntimeContext().resolve_one("Raw", registry, allowed_datasources={"Other"})

    cycle_registry = DatasourceRegistry()
    cycle_registry.register("A", DatasourceSpec("A", "A", ["B"], lambda _ctx: ContextValue("A", "a", "a", "A")))
    cycle_registry.register("B", DatasourceSpec("B", "B", ["A"], lambda _ctx: ContextValue("B", "b", "b", "B")))
    with pytest.raises(DatasourceResolutionCycle):
        await RuntimeContext().resolve_one("A", cycle_registry)

    ctx = DatasourceContext({"Visible": ContextValue("Visible", "v", "visible", "test")}, trace.trace)
    assert ctx.get("Visible").value == "v"
    with pytest.raises(MissingContextSlot):
        ctx.get("Missing")
    ctx.trace("custom.event", ok=True)
    assert ctx.redact("secret") == "[redacted]"

    tools = FakeToolRegistry(approval_callback=lambda _name, _kwargs: True)

    async def async_tool() -> dict[str, bool]:
        return {"ok": True}

    tools.register("async", async_tool, "preapproved")
    assert await tools.call("async", trace.trace) == {"ok": True}
    tools.register("denied", lambda: None, "denied")
    with pytest.raises(ToolPermissionDenied):
        await tools.call("denied", trace.trace)
    with pytest.raises(ToolExecutionFailed):
        await tools.call("missing", trace.trace)
    tools.register("broken", lambda: 1 / 0, "preapproved")
    with pytest.raises(ToolExecutionFailed):
        await tools.call("broken", trace.trace)
    assert trace.trace.count("tool.failed", "broken") == 1

    assert load_python_ref("contract4agents.runtime:ContextValue") is ContextValue
    with pytest.raises(ValueError):
        load_python_ref("badref")


def test_run_async_helper() -> None:
    async def async_tool() -> dict[str, bool]:
        return {"ok": True}

    assert run_async(async_tool()) == {"ok": True}


@pytest.mark.asyncio
async def test_eval_edges() -> None:
    class BadJudge:
        async def judge(self, *, output: dict[str, object], criterion: str) -> bool:
            return False

    runner = EvalRunner(
        {"Result": {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}}, BadJudge()
    )
    result = await runner.evaluate(
        name="bad",
        output={"ok": False, "message": "contains forbidden"},
        output_type="Result",
        trace=RuntimeContext().trace,
        expectations=[
            "output.ok != true",
            "output.message excludes forbidden",
            "trace.not_called(anything)",
            "output discovers hidden_truth.likely_cause",
        ],
        semantic_expectations=['semantic(output, "bad")'],
        hidden_truth={"likely_cause": "missing evidence"},
    )

    assert not result.passed
    assert {"output", "hidden_truth", "semantic"} <= {failure.kind for failure in result.failures}


def test_monitor_condition_and_success_paths() -> None:
    trace = RuntimeContext().trace
    assert run_monitors([MonitorRule("r", "A", "low", "", "")], trace) == []
    assert run_monitors([MonitorRule("r", "A", "low", "trace.tool_called(x)", 'trace.contains("x")')], trace) == []
    assert run_monitors([MonitorRule("r", "A", "low", "trace.bad(x)", "")], trace)[0].message.startswith(
        "Invalid monitor rule"
    )


def test_cli_error_paths(tmp_path: Path) -> None:
    runner = CliRunner()
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "bad.contract").write_text("wat")

    assert runner.invoke(main, ["check", str(bad)]).exit_code != 0
    assert runner.invoke(main, ["compile", str(bad), "--out", str(tmp_path / "out")]).exit_code != 0


def test_docs_broken_link(tmp_path: Path) -> None:
    for relative in [
        "VISION.md",
        "docs/index.md",
        "docs/decisions/accepted-decisions.md",
        "docs/architecture/parser-internals.md",
        "docs/reference/grammar.md",
        "docs/reference/manifest.md",
        "docs/reference/trace-schema.md",
        "docs/reference/eval-language.md",
        "docs/reference/cli.md",
        "docs/reference/openai-adapter.md",
        "docs/reference/semantic-judge.md",
        "docs/reference/test-fixtures.md",
        "docs/examples/incident-command-walkthrough.md",
    ]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[bad](missing.md)")

    diagnostics = check_docs(tmp_path)
    assert any(diagnostic.code == "DOC002" for diagnostic in diagnostics)


def test_openai_unavailable_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "agents", raising=False)
    monkeypatch.setitem(sys.modules, "agents", None)
    with pytest.raises(OpenAIAdapterUnavailable):
        build_openai_agent({"agent": "A"}, "instructions")
