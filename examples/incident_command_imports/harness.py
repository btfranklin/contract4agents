"""Incident Command fixture seeding and deterministic local harness."""

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
from examples.incident_command_imports import deploys, logs, metrics, status_page

SCENARIO_ID = "checkout-latency-2026-05-01"
FIXTURE_START_ID = "discovers_checkout_cause"


@dataclass(frozen=True)
class IncidentCommandStart:
    start_id: str
    approve_status_page: bool = False


def seed_incident_command(db_path: Path) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE services(id TEXT PRIMARY KEY, name TEXT NOT NULL, owner TEXT NOT NULL)")
        conn.execute(
            """
            CREATE TABLE incidents(
                id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL,
                start TEXT NOT NULL,
                end TEXT NOT NULL,
                summary TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE log_events(
                id INTEGER PRIMARY KEY,
                service_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE deploys(
                id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                sha TEXT NOT NULL,
                summary TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE metric_points(
                id INTEGER PRIMARY KEY,
                service_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE status_page_drafts(
                id INTEGER PRIMARY KEY,
                incident_id TEXT NOT NULL,
                message TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE scenario_truth(
                scenario_id TEXT PRIMARY KEY,
                likely_cause TEXT NOT NULL,
                required_evidence TEXT NOT NULL
            )
            """
        )
        conn.execute("INSERT INTO services VALUES (?, ?, ?)", ("checkout-api", "Checkout API", "payments"))
        conn.execute(
            "INSERT INTO incidents VALUES (?, ?, ?, ?, ?)",
            (
                SCENARIO_ID,
                "checkout-api",
                "2026-05-01T10:00:00Z",
                "2026-05-01T11:00:00Z",
                "Checkout latency and timeout spike",
            ),
        )
        conn.executemany(
            "INSERT INTO log_events(service_id, ts, level, message) VALUES (?, ?, ?, ?)",
            [
                ("checkout-api", "2026-05-01T10:05:00Z", "info", "deploy 8f31c2 completed"),
                ("checkout-api", "2026-05-01T10:12:00Z", "error", "payment provider timeout after 3000ms"),
                ("checkout-api", "2026-05-01T10:18:00Z", "error", "checkout request failed after payment timeout"),
            ],
        )
        conn.execute(
            "INSERT INTO deploys VALUES (?, ?, ?, ?, ?)",
            (
                "dep-001",
                "checkout-api",
                "2026-05-01T10:04:00Z",
                "8f31c2",
                "Changed payment timeout handling from adaptive to fixed 3000ms",
            ),
        )
        conn.executemany(
            "INSERT INTO metric_points(service_id, ts, metric, value) VALUES (?, ?, ?, ?)",
            [
                ("checkout-api", "2026-05-01T09:55:00Z", "p95_latency_ms", 220.0),
                ("checkout-api", "2026-05-01T10:15:00Z", "p95_latency_ms", 3200.0),
                ("checkout-api", "2026-05-01T10:20:00Z", "error_rate", 0.18),
            ],
        )
        conn.execute(
            "INSERT INTO scenario_truth VALUES (?, ?, ?)",
            (
                SCENARIO_ID,
                "deploy 8f31c2 changed payment timeout handling and caused checkout payment timeouts",
                json.dumps(["payment timeout logs", "deploy 8f31c2", "p95 latency spike"]),
            ),
        )
        conn.commit()
    return db_path


def load_hidden_truth(db_path: Path, scenario_id: str = SCENARIO_ID) -> dict[str, Any]:
    if scenario_id == FIXTURE_START_ID:
        scenario_id = SCENARIO_ID
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT likely_cause, required_evidence FROM scenario_truth WHERE scenario_id = ?",
            (scenario_id,),
        ).fetchone()
    if not row:
        return {}
    return {"likely_cause": row[0], "required_evidence": json.loads(row[1])}


def incident_command_starts() -> list[IncidentCommandStart]:
    return [IncidentCommandStart(FIXTURE_START_ID)]


async def run_incident_command_harness(
    db_path: Path, approve_status_page: bool = False, trace_path: Path | None = None
) -> tuple[dict[str, Any], TraceRecorder]:
    os.environ["CONTRACT4AGENTS_INCIDENT_DB"] = str(db_path)

    trace = TraceRecorder(trace_path)
    tools = FakeToolRegistry(approval_callback=lambda _name, _kwargs: approve_status_page)
    tools.register("logs.search", logs.search, "preapproved")
    tools.register("deploys.list", deploys.list, "preapproved")
    tools.register("metrics.query", metrics.query, "preapproved")
    tools.register("status_page.draft_update", status_page.draft_update, "requires_approval")
    log_results = await tools.call(
        "logs.search",
        trace,
        agent="LogInvestigator",
        service="checkout-api",
        start="2026-05-01T10:00:00Z",
        end="2026-05-01T11:00:00Z",
        query="timeout",
    )
    deploy_results = await tools.call(
        "deploys.list",
        trace,
        agent="DeployAnalyst",
        service="checkout-api",
        start="2026-05-01T10:00:00Z",
        end="2026-05-01T11:00:00Z",
    )
    metric_results = await tools.call(
        "metrics.query",
        trace,
        agent="MetricsAnalyst",
        service="checkout-api",
        metric="p95_latency_ms",
        start="2026-05-01T10:00:00Z",
        end="2026-05-01T11:00:00Z",
    )
    trace.record("agent.completed", agent="LogInvestigator")
    trace.record("agent.completed", agent="DeployAnalyst")
    trace.record("agent.completed", agent="MetricsAnalyst")
    output = {
        "summary": "Checkout API latency spiked after deploy 8f31c2.",
        "likely_cause": "deploy 8f31c2 changed payment timeout handling and caused checkout payment timeouts",
        "evidence": [
            log_results["events"][0]["message"],
            deploy_results["deploys"][0]["summary"],
            f"p95 latency reached {metric_results['max_value']}ms",
        ],
        "next_actions": ["roll back deploy 8f31c2", "restore adaptive timeout handling"],
    }
    trace.record("agent.completed", agent="IncidentCommander", output=output)
    return output, trace


def run_incident_command_harness_sync(
    db_path: Path, approve_status_page: bool = False
) -> tuple[dict[str, Any], TraceRecorder]:
    return asyncio.run(run_incident_command_harness(db_path, approve_status_page))


async def run_incident_command_start(
    start: IncidentCommandStart,
    db_path: Path,
    _artifacts: dict[str, Any],
    trace_path: Path,
) -> tuple[dict[str, Any], TraceRecorder]:
    return await run_incident_command_harness(db_path, start.approve_status_page, trace_path)
