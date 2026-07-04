"""Market Research Brief seeding and deterministic local harness."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contract4agents.runtime import FakeToolRegistry, TraceRecorder
from examples.market_research_brief_imports import citation, competitors, current_facts, documents

SCENARIO_ID = "field-ops-ai-2026-06"
FIXTURE_START_ID = "validates_segment_opportunity"


@dataclass(frozen=True)
class MarketResearchBriefStart:
    start_id: str


def seed_market_research_brief(db_path: Path) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE internal_documents(
                id TEXT PRIMARY KEY,
                document_type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE current_facts(
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                as_of_date TEXT NOT NULL,
                claim TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE competitor_snapshots(
                competitor TEXT NOT NULL,
                segment TEXT NOT NULL,
                positioning TEXT NOT NULL,
                recent_signal TEXT NOT NULL,
                citation TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE scenario_truth(
                scenario_id TEXT PRIMARY KEY,
                market_thesis TEXT NOT NULL,
                required_terms TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO internal_documents VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "doc-cust-001",
                    "customer",
                    "Field Ops Customer Interviews",
                    "Operations leaders want AI summaries of site visits, "
                    "but they distrust tools that cannot cite job notes.",
                    "2026-01-12",
                ),
                (
                    "doc-sales-002",
                    "sales",
                    "Lost Deals Review",
                    "Several field operations deals stalled because buyers asked "
                    "for competitor comparisons and compliance notes.",
                    "2025-11-03",
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO current_facts VALUES (?, ?, ?, ?)",
            [
                (
                    "fact-market-001",
                    "2026 field operations buyer survey",
                    "2026-06-01",
                    "Field operations buyers now prioritize auditable AI summaries with source citations.",
                ),
                (
                    "fact-compliance-002",
                    "2026 procurement benchmark",
                    "2026-05-15",
                    "Procurement teams prefer vendors that show freshness dates "
                    "and evidence trails for AI-generated reports.",
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO competitor_snapshots VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "OpsPilot",
                    "field operations",
                    "fast site-report automation",
                    "recently added generic AI summaries without customer-note citations",
                    "comp-ops-2026",
                ),
                (
                    "CrewLens",
                    "field operations",
                    "compliance-first workflow analytics",
                    "markets evidence trails but has limited mobile workflow support",
                    "comp-crew-2026",
                ),
            ],
        )
        conn.execute(
            "INSERT INTO scenario_truth VALUES (?, ?, ?)",
            (
                SCENARIO_ID,
                "auditable AI summaries for field operations are an attractive segment "
                "when citations and freshness notes are explicit",
                json.dumps(["auditable", "AI summaries", "field operations", "citations", "freshness notes"]),
            ),
        )
        conn.commit()
    return db_path


def load_hidden_truth(db_path: Path, scenario_id: str = SCENARIO_ID) -> dict[str, Any]:
    if scenario_id == FIXTURE_START_ID:
        scenario_id = SCENARIO_ID
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT market_thesis, required_terms FROM scenario_truth WHERE scenario_id = ?",
            (scenario_id,),
        ).fetchone()
    if not row:
        return {}
    return {"market_thesis": json.loads(row[1])}


def market_research_brief_starts() -> list[MarketResearchBriefStart]:
    return [MarketResearchBriefStart(FIXTURE_START_ID)]


async def run_market_research_brief_harness(
    db_path: Path,
    trace_path: Path | None = None,
) -> tuple[dict[str, Any], TraceRecorder]:
    os.environ["CONTRACT4AGENTS_MARKET_RESEARCH_DB"] = str(db_path)

    trace = TraceRecorder(trace_path)
    tools = FakeToolRegistry()
    tools.register("documents.search", documents.search, "preapproved")
    tools.register("documents.fetch", documents.fetch, "preapproved")
    tools.register("current_facts.search", current_facts.search, "preapproved")
    tools.register("current_facts.fetch", current_facts.fetch, "preapproved")
    tools.register("competitors.lookup", competitors.lookup, "preapproved")
    tools.register("citation.format", citation.format, "preapproved")

    doc_hits = await tools.call(
        "documents.search",
        trace,
        agent="MarketResearchLead",
        query="AI summaries",
        document_type="customer",
    )
    doc = await tools.call(
        "documents.fetch",
        trace,
        agent="MarketResearchLead",
        document_id=doc_hits["results"][0]["document_id"],
    )
    current_hits = await tools.call(
        "current_facts.search",
        trace,
        agent="MarketResearchLead",
        query="AI summaries",
        as_of_date="2026-06-15",
    )
    current_fact = await tools.call(
        "current_facts.fetch",
        trace,
        agent="MarketResearchLead",
        fact_id=current_hits["results"][0]["fact_id"],
    )
    competitor_result = await tools.call(
        "competitors.lookup",
        trace,
        agent="MarketResearchLead",
        segment="field operations",
    )
    customer_doc = await tools.call("documents.fetch", trace, agent="MarketResearchLead", document_id="doc-sales-002")
    doc_citation = await tools.call(
        "citation.format",
        trace,
        agent="MarketResearchLead",
        source_id=doc["document_id"],
        claim="customers distrust uncited AI summaries",
    )
    fact_citation = await tools.call(
        "citation.format",
        trace,
        agent="MarketResearchLead",
        source_id=current_fact["fact_id"],
        claim="buyers prioritize auditable AI summaries with source citations",
    )

    document_evidence = {
        "document_id": doc["document_id"],
        "title": doc["title"],
        "claim": "operations leaders want AI summaries but require citations to trust them",
        "citation": doc_citation["citation"],
    }
    trace.record("agent.completed", agent="DocumentAnalyst", output=document_evidence)

    current_evidence = {
        "fact_id": current_fact["fact_id"],
        "source": current_fact["source"],
        "as_of_date": current_fact["as_of_date"],
        "claim": current_fact["claim"],
        "citation": fact_citation["citation"],
    }
    trace.record("agent.completed", agent="CurrentTruthScout", output=current_evidence)

    competitor = competitor_result["competitors"][0]
    trace.record("agent.completed", agent="CompetitorAnalyst", output=competitor)

    customer_summary = {
        "pain_points": [
            "buyers distrust AI summaries without citations",
            "sales cycles stall when competitor and compliance evidence is missing",
        ],
        "segments": ["field operations"],
        "citations": [doc_citation["citation"], str(customer_doc["document_id"])],
    }
    trace.record("agent.completed", agent="CustomerSignalAnalyst", output=customer_summary)

    trace.record("agent.completed", agent="ReportWriter")
    output = {
        "thesis": "Auditable AI summaries for field operations are an attractive segment "
        "when citations and freshness notes are explicit.",
        "opportunity": "Package cited site-visit summaries for field operations teams that need evidence trails.",
        "current_facts": [current_fact["claim"]],
        "internal_evidence": [document_evidence["claim"]],
        "competitor_signals": [competitor["recent_signal"]],
        "customer_signals": customer_summary["pain_points"],
        "risks": ["stale internal documents could overstate market demand"],
        "recommendation": "Prototype a cited field-ops summary workflow and test it with compliance-sensitive buyers.",
        "citations": [doc_citation["citation"], fact_citation["citation"], competitor["citation"]],
        "freshness_notes": ["current fact snapshot as of 2026-06-01", "internal interviews from 2026-01-12"],
    }
    trace.record("agent.completed", agent="MarketResearchLead", output=output)
    return output, trace


def run_market_research_brief_harness_sync(db_path: Path) -> tuple[dict[str, Any], TraceRecorder]:
    return asyncio.run(run_market_research_brief_harness(db_path))


async def run_market_research_brief_start(
    _start: MarketResearchBriefStart,
    db_path: Path,
    _artifacts: dict[str, Any],
    trace_path: Path,
) -> tuple[dict[str, Any], TraceRecorder]:
    return await run_market_research_brief_harness(db_path, trace_path)
