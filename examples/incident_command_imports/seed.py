"""Deterministic teaching data for the contract-first Incident Command example."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


def seed_incident_data(db_path: Path) -> Path:
    """Create a fresh, deterministic incident dataset at ``db_path``."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE services(
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                owner TEXT NOT NULL
            );
            CREATE TABLE incidents(
                id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL,
                start TEXT NOT NULL,
                end TEXT NOT NULL,
                summary TEXT NOT NULL
            );
            CREATE TABLE log_events(
                id INTEGER PRIMARY KEY,
                service_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            );
            CREATE TABLE deploys(
                id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                sha TEXT NOT NULL,
                summary TEXT NOT NULL
            );
            CREATE TABLE metric_points(
                id INTEGER PRIMARY KEY,
                service_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL
            );
            CREATE TABLE status_page_drafts(
                id INTEGER PRIMARY KEY,
                incident_id TEXT NOT NULL,
                message TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO services VALUES (?, ?, ?)",
            ("checkout-api", "Checkout API", "payments"),
        )
        connection.execute(
            "INSERT INTO incidents VALUES (?, ?, ?, ?, ?)",
            (
                "checkout-latency-2026-05-01",
                "checkout-api",
                "2026-05-01T10:00:00Z",
                "2026-05-01T11:00:00Z",
                "Checkout latency and timeout spike",
            ),
        )
        connection.executemany(
            "INSERT INTO log_events(service_id, ts, level, message) VALUES (?, ?, ?, ?)",
            (
                ("checkout-api", "2026-05-01T10:05:00Z", "info", "deploy 8f31c2 completed"),
                (
                    "checkout-api",
                    "2026-05-01T10:12:00Z",
                    "error",
                    "payment provider timeout after 3000ms",
                ),
                (
                    "checkout-api",
                    "2026-05-01T10:18:00Z",
                    "error",
                    "checkout request failed after payment timeout",
                ),
            ),
        )
        connection.execute(
            "INSERT INTO deploys VALUES (?, ?, ?, ?, ?)",
            (
                "dep-001",
                "checkout-api",
                "2026-05-01T10:04:00Z",
                "8f31c2",
                "Changed payment timeout handling from adaptive to fixed 3000ms",
            ),
        )
        connection.executemany(
            "INSERT INTO metric_points(service_id, ts, metric, value) VALUES (?, ?, ?, ?)",
            (
                ("checkout-api", "2026-05-01T09:55:00Z", "p95_latency_ms", 220.0),
                ("checkout-api", "2026-05-01T10:15:00Z", "p95_latency_ms", 3200.0),
                ("checkout-api", "2026-05-01T10:20:00Z", "error_rate", 0.18),
            ),
        )
        connection.commit()
    return db_path


__all__ = ["seed_incident_data"]
