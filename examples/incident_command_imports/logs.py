from __future__ import annotations

from contextlib import closing

from examples.incident_command_imports._db import connect


def search(service: str, start: str, end: str, query: str) -> dict[str, object]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT ts, level, message
            FROM log_events
            WHERE service_id = ? AND ts >= ? AND ts <= ? AND message LIKE ?
            ORDER BY ts
            """,
            (service, start, end, f"%{query}%"),
        ).fetchall()
    return {"events": [{"ts": row[0], "level": row[1], "message": row[2]} for row in rows]}
