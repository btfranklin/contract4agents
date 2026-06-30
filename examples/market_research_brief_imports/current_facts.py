from __future__ import annotations

from contextlib import closing

from examples.market_research_brief_imports._db import connect


def search(query: str, as_of_date: str) -> dict[str, object]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT id, source, as_of_date, substr(claim, 1, 140)
            FROM current_facts
            WHERE as_of_date <= ? AND claim LIKE ?
            ORDER BY as_of_date DESC, id
            """,
            (as_of_date, f"%{query}%"),
        ).fetchall()
    return {
        "results": [
            {"fact_id": row[0], "source": row[1], "as_of_date": row[2], "snippet": row[3]}
            for row in rows
        ]
    }


def fetch(fact_id: str) -> dict[str, object]:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT id, source, as_of_date, claim FROM current_facts WHERE id = ?",
            (fact_id,),
        ).fetchone()
    if not row:
        return {"fact_id": fact_id, "found": False}
    return {"fact_id": row[0], "source": row[1], "as_of_date": row[2], "claim": row[3]}
