from __future__ import annotations


def request(topic: str, summary: str) -> dict[str, object]:
    return {"topic": topic, "summary": summary, "requested": True}
