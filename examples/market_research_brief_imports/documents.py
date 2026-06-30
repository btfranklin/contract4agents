from __future__ import annotations

from contextlib import closing

from examples.market_research_brief_imports._db import connect


def search(query: str, document_type: str) -> dict[str, object]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT id, title, document_type, substr(body, 1, 120)
            FROM internal_documents
            WHERE document_type = ? AND (title LIKE ? OR body LIKE ?)
            ORDER BY id
            """,
            (document_type, f"%{query}%", f"%{query}%"),
        ).fetchall()
    return {
        "results": [
            {"document_id": row[0], "title": row[1], "document_type": row[2], "snippet": row[3]}
            for row in rows
        ]
    }


def fetch(document_id: str) -> dict[str, object]:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT id, title, document_type, body, created_at FROM internal_documents WHERE id = ?",
            (document_id,),
        ).fetchone()
    if not row:
        return {"document_id": document_id, "found": False}
    return {
        "document_id": row[0],
        "title": row[1],
        "document_type": row[2],
        "body": row[3],
        "created_at": row[4],
    }
