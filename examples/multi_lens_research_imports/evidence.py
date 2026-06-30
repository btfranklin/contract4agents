from __future__ import annotations

from contextlib import closing

from examples.multi_lens_research_imports._db import connect


def score(source_id: str, claim: str) -> dict[str, object]:
    with closing(connect()) as conn:
        row = conn.execute("SELECT quality, lens FROM sources WHERE id = ?", (source_id,)).fetchone()
    if not row:
        return {"source_id": source_id, "score": 0.0, "rationale": "source not found"}
    quality = float(row[0])
    score_value = round(quality if claim else quality * 0.5, 2)
    return {
        "source_id": source_id,
        "score": score_value,
        "rationale": f"{row[1]} source supports a specific claim",
    }
