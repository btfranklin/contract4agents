from __future__ import annotations

from pathlib import Path

from contract4agents.compiler import compile_project
from contract4agents.evaluation import EvalRunner
from examples.incident_command_imports.harness import (
    load_hidden_truth,
    run_incident_command_harness_sync,
    seed_incident_command,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "examples" / "incident-command"


def test_incident_command_local_end_to_end(tmp_path: Path) -> None:
    db_path = seed_incident_command(tmp_path / "fixture.sqlite")
    artifacts = compile_project(FIXTURE)
    output, trace = run_incident_command_harness_sync(db_path)
    hidden_truth = load_hidden_truth(db_path)
    runner = EvalRunner(artifacts["schemas"])

    import asyncio

    result = asyncio.run(
        runner.evaluate(
            name="discovers_checkout_cause",
            output=output,
            trace=trace,
            expectations=artifacts["evals"][0]["expects"],
            semantic_expectations=artifacts["evals"][0]["semantic_expects"],
            hidden_truth=hidden_truth,
        )
    )

    assert result.passed
    assert result.skipped_semantic
