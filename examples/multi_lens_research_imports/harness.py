"""Multi-Lens Research seeding and deterministic local harness."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from contract4agents.runtime import FakeToolRegistry, TraceRecorder
from examples.multi_lens_research_imports import citation, evidence, expert_review, sources

SCENARIO_ID = "staged-eval-rollout-2026-06"


def seed_multi_lens_research(db_path: Path) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE sources(
                id TEXT PRIMARY KEY,
                lens TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                quality REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE scenario_truth(
                scenario_id TEXT PRIMARY KEY,
                likely_conclusion TEXT NOT NULL,
                required_terms TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO sources VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "src-eval-001",
                    "evidence",
                    "Evaluation Gate Pilot Notes",
                    "The staged eval rollout reduced bad recommendations when releases were gated by scenario tests.",
                    0.94,
                ),
                (
                    "src-tech-002",
                    "technical",
                    "Platform Reliability Memo",
                    "A narrow release gate is feasible if traces, schemas, and rollback ownership are explicit.",
                    0.9,
                ),
                (
                    "src-policy-003",
                    "policy",
                    "Regulated Workflow Review",
                    "Human review is required before using automated recommendations in regulated workflows.",
                    0.88,
                ),
                (
                    "src-counter-004",
                    "counterargument",
                    "Adoption Risk Interview",
                    "Teams may bypass eval gates if the rollout slows urgent fixes or lacks clear ownership.",
                    0.82,
                ),
            ],
        )
        conn.execute(
            "INSERT INTO scenario_truth VALUES (?, ?, ?)",
            (
                SCENARIO_ID,
                "adopt a staged rollout with eval gates and human review for regulated workflows",
                json.dumps(["staged rollout", "eval gates", "human review", "regulated workflows"]),
            ),
        )
        conn.commit()
    return db_path


def load_hidden_truth(db_path: Path, scenario_id: str = SCENARIO_ID) -> dict[str, Any]:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT likely_conclusion, required_terms FROM scenario_truth WHERE scenario_id = ?",
            (scenario_id,),
        ).fetchone()
    if not row:
        return {}
    return {"likely_conclusion": json.loads(row[1])}


async def run_multi_lens_research_harness(
    db_path: Path, approve_expert_review: bool = False
) -> tuple[dict[str, Any], TraceRecorder]:
    os.environ["CONTRACT4AGENTS_MULTI_LENS_DB"] = str(db_path)

    trace = TraceRecorder()
    tools = FakeToolRegistry(approval_callback=lambda _name, _kwargs: approve_expert_review)
    tools.register("sources.search", sources.search, "preapproved")
    tools.register("sources.fetch", sources.fetch, "preapproved")
    tools.register("evidence.score", evidence.score, "preapproved")
    tools.register("citation.format", citation.format, "preapproved")
    tools.register("expert_review.request", expert_review.request, "requires_approval")

    evidence_hits = await tools.call("sources.search", trace, query="eval rollout", lens="evidence")
    technical_source = await tools.call("sources.fetch", trace, source_id="src-tech-002")
    policy_source = await tools.call("sources.fetch", trace, source_id="src-policy-003")
    counter_hits = await tools.call("sources.search", trace, query="bypass eval gates", lens="counterargument")
    counter_source = await tools.call("sources.fetch", trace, source_id="src-counter-004")
    eval_score = await tools.call(
        "evidence.score",
        trace,
        source_id="src-eval-001",
        claim="staged eval rollout reduced bad recommendations",
    )
    policy_score = await tools.call(
        "evidence.score",
        trace,
        source_id="src-policy-003",
        claim="human review is required for regulated workflows",
    )
    citation_result = await tools.call(
        "citation.format",
        trace,
        source_id="src-eval-001",
        claim="staged rollout with eval gates reduced bad recommendations",
    )

    evidence_map = {
        "topic": "staged eval rollout",
        "source_ids": ["src-eval-001", "src-tech-002", "src-policy-003", "src-counter-004"],
        "claims": [
            evidence_hits["results"][0]["snippet"],
            technical_source["body"],
            policy_source["body"],
        ],
        "citations": [citation_result["citation"]],
        "quality_notes": [
            f"eval evidence score {eval_score['score']}",
            f"policy evidence score {policy_score['score']}",
        ],
    }
    trace.record("agent.completed", agent="EvidenceMapper", output=evidence_map)

    technical = {
        "feasibility": "A narrow staged rollout is feasible with explicit trace, schema, and rollback ownership.",
        "implementation_risks": ["teams may bypass gates during urgent fixes"],
        "required_controls": ["release gate", "rollback owner", "trace review"],
        "citations": ["src-tech-002"],
    }
    trace.record("agent.completed", agent="TechnicalLensAnalyst", output=technical)

    policy_safety = {
        "policy_risks": ["regulated workflows need human review"],
        "safety_risks": ["unsupported recommendations could reach users without review"],
        "mitigation_requirements": ["human review for regulated workflows", "documented exception path"],
        "citations": ["src-policy-003"],
    }
    trace.record("agent.completed", agent="PolicySafetyLensAnalyst", output=policy_safety)

    counterarguments = {
        "strongest_counterarguments": [counter_source["body"]],
        "weak_assumptions": ["teams will accept added release friction"],
        "disconfirming_sources": [counter_hits["results"][0]["source_id"]],
        "citations": ["src-counter-004"],
    }
    trace.record("agent.completed", agent="CounterargumentAnalyst", output=counterarguments)

    trace.record("agent.completed", agent="SynthesisWriter")
    output = {
        "summary": "Adopt a staged rollout for agent recommendations only after eval gates are in place.",
        "recommendation": "Adopt a staged rollout with eval gates and human review for regulated workflows.",
        "confidence": 0.82,
        "key_findings": [
            "Evaluation gates reduced bad recommendations in the seeded pilot.",
            "Technical controls are feasible when trace and rollback ownership are explicit.",
        ],
        "risks": ["release friction", "regulated workflow review gaps"],
        "counterarguments": counterarguments["strongest_counterarguments"],
        "citations": ["src-eval-001", "src-tech-002", "src-policy-003", "src-counter-004"],
        "next_actions": ["pilot one team", "define gate ownership", "require human review for regulated workflows"],
    }
    trace.record("agent.completed", agent="ResearchDirector", output=output)
    return output, trace


def run_multi_lens_research_harness_sync(
    db_path: Path, approve_expert_review: bool = False
) -> tuple[dict[str, Any], TraceRecorder]:
    return asyncio.run(run_multi_lens_research_harness(db_path, approve_expert_review))
