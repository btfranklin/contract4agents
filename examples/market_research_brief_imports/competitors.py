from __future__ import annotations

from contextlib import closing

from examples.market_research_brief_imports._db import connect


def lookup(segment: str) -> dict[str, object]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT competitor, positioning, recent_signal, citation
            FROM competitor_snapshots
            WHERE segment = ?
            ORDER BY competitor
            """,
            (segment,),
        ).fetchall()
    return {
        "competitors": [
            {"competitor": row[0], "positioning": row[1], "recent_signal": row[2], "citation": row[3]}
            for row in rows
        ]
    }
