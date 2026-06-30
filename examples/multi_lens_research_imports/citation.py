from __future__ import annotations

from contextlib import closing

from examples.multi_lens_research_imports._db import connect


def format(source_id: str, claim: str) -> dict[str, object]:
    with closing(connect()) as conn:
        row = conn.execute("SELECT title FROM sources WHERE id = ?", (source_id,)).fetchone()
    title = row[0] if row else "unknown source"
    return {"citation": f"{claim} [{source_id}: {title}]"}
