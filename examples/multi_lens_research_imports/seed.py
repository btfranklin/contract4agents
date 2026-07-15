"""Deterministic teaching data for the contract-first Multi-Lens example."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


def seed_research_data(db_path: Path) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
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
        connection.executemany(
            "INSERT INTO sources VALUES (?, ?, ?, ?, ?)",
            (
                (
                    "src-eval-001",
                    "evidence",
                    "Evaluation Gate Pilot Notes",
                    "A staged eval rollout reduced bad recommendations when scenario tests gated releases.",
                    0.94,
                ),
                (
                    "src-tech-002",
                    "technical",
                    "Platform Reliability Memo",
                    "A narrow release gate is feasible when traces, schemas, and rollback ownership are explicit.",
                    0.90,
                ),
                (
                    "src-policy-003",
                    "policy",
                    "Regulated Workflow Review",
                    "Human review is required before automated recommendations enter regulated workflows.",
                    0.88,
                ),
                (
                    "src-counter-004",
                    "counterargument",
                    "Adoption Risk Interview",
                    "Teams may bypass eval gates when urgent fixes lack a clear exception owner.",
                    0.82,
                ),
            ),
        )
        connection.commit()
    return db_path


__all__ = ["seed_research_data"]
