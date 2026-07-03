"""Host-code surfaces for capability registry drift tests."""

from __future__ import annotations

from pydantic import BaseModel


class DriftResultModel(BaseModel):
    summary: str
    score: int


class DriftWrongResultModel(BaseModel):
    summary: str
    score: str


NOT_CALLABLE = 42


def lookup(query: str) -> dict[str, object]:
    return {"query": query, "summary": "known host result"}


def build_web_search_tool() -> object:
    return object()


def build_agent_factory() -> object:
    return object()
