from __future__ import annotations

from contextlib import closing

from examples.incident_command_imports._db import connect


def list(service: str, start: str, end: str) -> dict[str, object]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT id, ts, sha, summary
            FROM deploys
            WHERE service_id = ? AND ts >= ? AND ts <= ?
            ORDER BY ts
            """,
            (service, start, end),
        ).fetchall()
    return {
        "deploys": [
            f"{row[0]} at {row[1]} ({row[2]}): {row[3]}"
            for row in rows
        ]
    }
