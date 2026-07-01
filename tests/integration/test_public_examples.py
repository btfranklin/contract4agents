from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from contract4agents.cli import main
from contract4agents.compiler import CompilerArtifacts, compile_project
from contract4agents.evaluation import EvalRunner
from contract4agents.monitor import MonitorRule, run_monitors
from contract4agents.runtime import TraceRecorder
from examples.market_research_brief_imports.harness import (
    load_hidden_truth as load_market_hidden_truth,
)
from examples.market_research_brief_imports.harness import (
    run_market_research_brief_harness_sync,
    seed_market_research_brief,
)
from examples.multi_lens_research_imports.harness import (
    load_hidden_truth as load_multi_lens_hidden_truth,
)
from examples.multi_lens_research_imports.harness import (
    run_multi_lens_research_harness_sync,
    seed_multi_lens_research,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_EXAMPLES = [
    ROOT / "examples" / "incident-command",
    ROOT / "examples" / "multi-lens-research",
    ROOT / "examples" / "market-research-brief",
]


@pytest.mark.parametrize("example_root", PUBLIC_EXAMPLES, ids=lambda path: path.name)
def test_public_example_check_compile_visualize_smoke(example_root: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    build_dir = tmp_path / example_root.name / "build"
    visualization_dir = build_dir / "visualization"

    check_result = runner.invoke(main, ["check", str(example_root)])
    assert check_result.exit_code == 0, check_result.output

    compile_result = runner.invoke(main, ["compile", str(example_root), "--out", str(build_dir)])
    assert compile_result.exit_code == 0, compile_result.output
    assert (build_dir / "docs" / "summary.md").exists()

    visualize_result = runner.invoke(main, ["visualize", str(example_root), "--out", str(visualization_dir)])
    assert visualize_result.exit_code == 0, visualize_result.output
    assert (visualization_dir / "index.html").exists()


def test_multi_lens_research_local_end_to_end(tmp_path: Path) -> None:
    example_root = ROOT / "examples" / "multi-lens-research"
    db_path = seed_multi_lens_research(tmp_path / "multi-lens.sqlite")
    artifacts = compile_project(example_root)
    output, trace = run_multi_lens_research_harness_sync(db_path)
    hidden_truth = load_multi_lens_hidden_truth(db_path)

    result = asyncio.run(_evaluate_first_case(artifacts, output, "ResearchBrief", trace, hidden_truth))

    assert result.passed
    assert result.skipped_semantic


def test_multi_lens_research_monitor_catches_unapproved_expert_review() -> None:
    artifacts = compile_project(ROOT / "examples" / "multi-lens-research")
    trace = TraceRecorder()
    trace.record("approval.requested", tool="expert_review.request")

    violations = run_monitors(_monitor_rules(artifacts), trace)

    assert violations
    assert violations[0].rule == "expert_review_requires_approval"


def test_market_research_brief_local_end_to_end(tmp_path: Path) -> None:
    example_root = ROOT / "examples" / "market-research-brief"
    db_path = seed_market_research_brief(tmp_path / "market-research.sqlite")
    artifacts = compile_project(example_root)
    output, trace = run_market_research_brief_harness_sync(db_path)
    hidden_truth = load_market_hidden_truth(db_path)

    result = asyncio.run(_evaluate_first_case(artifacts, output, "MarketOpportunityReport", trace, hidden_truth))

    assert result.passed
    assert result.skipped_semantic


def test_market_research_monitor_catches_missing_current_facts() -> None:
    artifacts = compile_project(ROOT / "examples" / "market-research-brief")
    trace = TraceRecorder()
    trace.record("tool.completed", tool="documents.fetch")

    violations = run_monitors(_monitor_rules(artifacts), trace)

    assert violations
    assert violations[0].rule == "current_claims_need_current_facts"


def test_incident_command_fixture_eval_smoke() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["eval", str(ROOT / "examples" / "incident-command")])

    assert result.exit_code == 0, result.output
    assert "Fixture eval passed: 1 starts" in result.output
    assert "PASS discovers_checkout_cause" in result.output


async def _evaluate_first_case(
    artifacts: CompilerArtifacts,
    output: dict[str, Any],
    output_type: str,
    trace: TraceRecorder,
    hidden_truth: dict[str, Any],
) -> Any:
    runner = EvalRunner(artifacts["schemas"])
    eval_case = artifacts["evals"][0]
    return await runner.evaluate(
        name=eval_case["name"],
        output=output,
        output_type=output_type,
        trace=trace,
        expectations=eval_case["expects"],
        semantic_expectations=eval_case["semantic_expects"],
        hidden_truth=hidden_truth,
    )


def _monitor_rules(artifacts: CompilerArtifacts) -> list[MonitorRule]:
    return [
        MonitorRule(item["name"], item["agent"], item["severity"], item["when"], item["expect"])
        for item in artifacts["monitors"]
    ]
