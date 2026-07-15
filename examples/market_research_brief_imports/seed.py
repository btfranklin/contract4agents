"""Deterministic teaching data for the contract-first Market Research example."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


def seed_market_data(db_path: Path) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE internal_documents(
                id TEXT PRIMARY KEY,
                document_type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE current_facts(
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                as_of_date TEXT NOT NULL,
                claim TEXT NOT NULL
            );
            CREATE TABLE competitor_snapshots(
                competitor TEXT NOT NULL,
                segment TEXT NOT NULL,
                positioning TEXT NOT NULL,
                recent_signal TEXT NOT NULL,
                citation TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO internal_documents VALUES (?, ?, ?, ?, ?)",
            (
                (
                    "doc-cust-001",
                    "customer",
                    "Field Ops Customer Interviews",
                    "Operations leaders want AI site-visit summaries but distrust uncited results.",
                    "2026-01-12",
                ),
                (
                    "doc-sales-002",
                    "sales",
                    "Lost Deals Review",
                    "Field operations buyers requested competitor comparisons and compliance notes.",
                    "2025-11-03",
                ),
            ),
        )
        connection.executemany(
            "INSERT INTO current_facts VALUES (?, ?, ?, ?)",
            (
                (
                    "fact-market-001",
                    "2026 field operations buyer survey",
                    "2026-06-01",
                    "Buyers prioritize auditable AI summaries with source citations.",
                ),
                (
                    "fact-compliance-002",
                    "2026 procurement benchmark",
                    "2026-05-15",
                    "Procurement teams prefer freshness dates and evidence trails in AI reports.",
                ),
            ),
        )
        connection.executemany(
            "INSERT INTO competitor_snapshots VALUES (?, ?, ?, ?, ?)",
            (
                (
                    "OpsPilot",
                    "field operations",
                    "fast site-report automation",
                    "Added generic AI summaries without customer-note citations.",
                    "comp-ops-2026",
                ),
                (
                    "CrewLens",
                    "field operations",
                    "compliance-first workflow analytics",
                    "Markets evidence trails but has limited mobile workflow support.",
                    "comp-crew-2026",
                ),
            ),
        )
        connection.commit()
    return db_path


__all__ = ["seed_market_data"]
