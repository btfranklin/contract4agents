from __future__ import annotations

from contextlib import closing

from examples.multi_lens_research_imports._db import connect


def search(query: str, lens: str) -> dict[str, object]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT id, title, lens, substr(body, 1, 120)
            FROM sources
            WHERE lens = ? AND (body LIKE ? OR title LIKE ?)
            ORDER BY quality DESC, id
            """,
            (lens, f"%{query}%", f"%{query}%"),
        ).fetchall()
    return {
        "results": [
            {"source_id": row[0], "title": row[1], "lens": row[2], "snippet": row[3]}
            for row in rows
        ]
    }


def fetch(source_id: str) -> dict[str, object]:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT id, title, lens, body, quality FROM sources WHERE id = ?",
            (source_id,),
        ).fetchone()
    if not row:
        return {"source_id": source_id, "found": False}
    return {
        "source_id": row[0],
        "title": row[1],
        "lens": row[2],
        "body": row[3],
        "quality": float(row[4]),
    }
