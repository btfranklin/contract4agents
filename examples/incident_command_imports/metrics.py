from __future__ import annotations

from contextlib import closing

from examples.incident_command_imports._db import connect


def query(service: str, metric: str, start: str, end: str) -> dict[str, object]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT ts, value
            FROM metric_points
            WHERE service_id = ? AND metric = ? AND ts >= ? AND ts <= ?
            ORDER BY ts
            """,
            (service, metric, start, end),
        ).fetchall()
    values = [float(row[1]) for row in rows]
    maximum = max(values) if values else 0.0
    return {
        "metric": metric,
        "max_value": maximum,
        "summary": f"{metric} reached {maximum:g} across {len(values)} points.",
    }
