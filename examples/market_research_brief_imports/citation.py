from __future__ import annotations


def format(source_id: str, claim: str) -> dict[str, object]:
    return {"citation": f"{claim} [{source_id}]"}
