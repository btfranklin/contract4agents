from __future__ import annotations

from contextlib import closing

from examples.incident_command_imports._db import connect


def draft_update(incident_id: str, message: str) -> dict[str, object]:
    with closing(connect()) as conn:
        cursor = conn.execute(
            "INSERT INTO status_page_drafts(incident_id, message) VALUES (?, ?)",
            (incident_id, message),
        )
        conn.commit()
    return {"draft_id": cursor.lastrowid, "incident_id": incident_id, "message": message}
