from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from click.testing import CliRunner

from contract4agents import compile_project, materialize
from contract4agents.assurance import (
    RunSpecAssessmentInput,
    RunSpecAssessmentManifest,
    RunSpecEvidence,
    RunSpecSelection,
    RunSpecStageObservation,
)
from contract4agents.cli import main
from contract4agents.eval_campaigns import CampaignConfig, FileEvalProvider, run_campaign
from contract4agents.ir import semantic_id
from contract4agents.tracing import (
    NormalizedTrace,
    TraceClosureManifest,
    TraceFrontier,
    write_trace_jsonl,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "incident-command"


def test_cli_reports_invalid_normalized_trace(tmp_path: Path) -> None:
    trace_path = tmp_path / "bad.jsonl"
    trace_path.write_text("{bad\n")

    result = CliRunner().invoke(
        main,
        [
            "assess",
            str(EXAMPLE),
            "--target",
            "openai",
            "--profile",
            "test",
            "--trace",
            str(trace_path),
        ],
    )

    assert result.exit_code != 0
    assert "Invalid normalized trace" in result.output
    assert "line 1" in result.output


def test_cli_assure_assesses_versioned_run_spec_evidence(tmp_path: Path) -> None:
    project = ROOT / "examples" / "multi-lens-research"
    artifacts = compile_project(project)
    materialized = materialize(project, "openai", "test")
    campaign = asyncio.run(
        run_campaign(
            artifacts.ir,
            materialized.plan,
            FileEvalProvider.load(project / "eval-data.json"),
            CampaignConfig("run-spec-cli"),
        )
    )
    trial = campaign.cases[0].trials[0]
    assert trial.trace is not None and trial.trace_closure is not None
    assurance_trace = NormalizedTrace(
        tuple(
            replace(event, parent_event_id=None)
            for event in trial.trace.events
            if not (
                event.event_type == "agent.started"
                and event.semantic.agent_id == semantic_id("agent", "ResearchDirector")
            )
        )
    )
    trace_path = tmp_path / "trace.jsonl"
    closure_path = tmp_path / "trace-closure.json"
    run_spec_path = tmp_path / "run-spec-evidence.json"
    eval_path = tmp_path / "eval.json"
    provenance_path = tmp_path / "provenance.json"
    materialization_path = tmp_path / "materialization-conformance.json"
    output_dir = tmp_path / "assurance"
    write_trace_jsonl(trace_path, assurance_trace)
    closure_path.write_text(
        TraceClosureManifest(
            (replace(trial.trace_closure, frontier=TraceFrontier.from_trace(assurance_trace)),)
        ).to_json()
    )
    event_ids = {
        event.semantic.agent_id.parts[-1]: event.event_id
        for event in assurance_trace.events
        if event.event_type == "agent.completed" and event.semantic.agent_id is not None
    }
    observations = (
        RunSpecStageObservation(
            "evidence-1",
            "evidence",
            semantic_id("agent", "EvidenceMapper"),
            {
                "topic": "evaluation rollout",
                "source_ids": ["SRC-001", "SRC-002"],
                "claims": ["staged rollout reduces risk"],
                "citations": ["[SRC-001]", "[SRC-002]"],
                "quality_notes": ["representative sources"],
            },
            (event_ids["EvidenceMapper"],),
        ),
        RunSpecStageObservation(
            "technical-1",
            "technical",
            semantic_id("agent", "TechnicalLensAnalyst"),
            {
                "feasibility": "high",
                "implementation_risks": ["drift"],
                "required_controls": ["regression gates"],
                "citations": ["[SRC-001]"],
            },
            (event_ids["TechnicalLensAnalyst"],),
        ),
        RunSpecStageObservation(
            "policy-1",
            "policy_safety",
            semantic_id("agent", "PolicySafetyLensAnalyst"),
            {
                "policy_risks": ["coverage gaps"],
                "safety_risks": ["unsafe automation"],
                "mitigation_requirements": ["human review"],
                "citations": ["[SRC-002]"],
            },
            (event_ids["PolicySafetyLensAnalyst"],),
        ),
        RunSpecStageObservation(
            "counterarguments-1",
            "counterarguments",
            semantic_id("agent", "CounterargumentAnalyst"),
            {
                "strongest_counterarguments": ["a single launch is faster"],
                "weak_assumptions": ["fixtures are representative"],
                "disconfirming_sources": ["SRC-002"],
                "citations": ["[SRC-002]"],
            },
            (event_ids["CounterargumentAnalyst"],),
        ),
        RunSpecStageObservation(
            "brief-1",
            "final_brief",
            semantic_id("agent", "ResearchDirector"),
            trial.output,
            (event_ids["ResearchDirector"],),
        ),
    )
    selection = RunSpecSelection(
        assurance_trace.run_ids[0],
        "run_spec:MultiLensResearchRun",
        "The host workflow selected the multi-lens run spec.",
        ("workflow-ledger:selection",),
    )
    manifest = RunSpecAssessmentManifest(
        (
            RunSpecAssessmentInput(
                selection,
                RunSpecEvidence(
                    "complete",
                    "The host workflow ledger is closed.",
                    observations,
                    derived_values={
                        "evidence_source_ids": ["SRC-001", "SRC-002"],
                        "final_citation_ids": ["SRC-001", "SRC-002"],
                    },
                    evidence_refs=("workflow-ledger:run",),
                ),
            ),
        )
    )
    run_spec_path.write_text(json.dumps(manifest.to_dict()))
    eval_path.write_text(json.dumps(campaign.to_dict()))
    provenance_path.write_text(json.dumps({"source": "unit-test"}))
    materialization_path.write_text(json.dumps(materialized.graph.validation.to_dict()))

    result = CliRunner().invoke(
        main,
        [
            "assure",
            str(project),
            "--target",
            "openai",
            "--profile",
            "test",
            "--trace",
            str(trace_path),
            "--materialization-evidence",
            str(materialization_path),
            "--trace-closure",
            str(closure_path),
            "--run-spec-evidence",
            str(run_spec_path),
            "--eval-results",
            str(eval_path),
            "--provenance",
            str(provenance_path),
            "--out",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    attestation = json.loads((output_dir / "attestation.json").read_text())
    run_specs = json.loads((output_dir / "run-spec-results.json").read_text())
    assert attestation["complete"] is True
    assert run_specs["results"][0]["status"] == "passed"
