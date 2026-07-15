from __future__ import annotations

from contextlib import closing

from examples.incident_command_imports._db import connect


def service(service_id: str) -> dict[str, object]:
    with closing(connect()) as connection:
        row = connection.execute(
            "SELECT id, name, owner FROM services WHERE id = ?",
            (service_id,),
        ).fetchone()
    if row is None:
        raise LookupError(f"Unknown incident service `{service_id}`")
    return {"id": row[0], "name": row[1], "owner": row[2]}


def active_incident() -> dict[str, object]:
    with closing(connect()) as connection:
        row = connection.execute(
            "SELECT service_id, start, end, summary FROM incidents ORDER BY start DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise LookupError("No active incident is available")
    return {"service": row[0], "start": row[1], "end": row[2], "symptom": row[3]}
