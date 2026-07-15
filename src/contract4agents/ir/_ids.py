"""Stable kind-qualified semantic identifiers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

SemanticKind = Literal[
    "type",
    "tool",
    "datasource",
    "external",
    "context",
    "agent",
    "grant",
    "edge",
    "control",
    "quality",
    "operational",
    "isolation",
    "eval",
    "run_spec",
]

SEMANTIC_KINDS: frozenset[str] = frozenset(
    {
        "type",
        "tool",
        "datasource",
        "external",
        "context",
        "agent",
        "grant",
        "edge",
        "control",
        "quality",
        "operational",
        "isolation",
        "eval",
        "run_spec",
    }
)


@dataclass(frozen=True, order=True)
class SemanticId:
    """A readable semantic identity shared by IR, plans, traces, and assurance."""

    kind: SemanticKind
    parts: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in SEMANTIC_KINDS:
            raise ValueError(f"Unknown semantic ID kind `{self.kind}`")
        if not self.parts:
            raise ValueError("A semantic ID requires at least one name part")
        for part in self.parts:
            if not part or part != part.strip() or ":" in part:
                raise ValueError(f"Invalid semantic ID part `{part}`")

    @property
    def value(self) -> str:
        return ":".join((self.kind, *self.parts))

    def __str__(self) -> str:
        return self.value

    @classmethod
    def parse(cls, value: str) -> SemanticId:
        kind, separator, remainder = value.partition(":")
        if not separator or not remainder or kind not in SEMANTIC_KINDS:
            raise ValueError(f"Invalid semantic ID `{value}`")
        return cls(cast(SemanticKind, kind), tuple(remainder.split(":")))

    def require_kind(self, *expected: SemanticKind) -> SemanticId:
        if self.kind not in expected:
            allowed = ", ".join(expected)
            raise ValueError(f"Semantic ID `{self}` must have kind {allowed}")
        return self


def semantic_id(kind: SemanticKind, *parts: str) -> SemanticId:
    """Construct a validated kind-qualified semantic ID."""

    return SemanticId(kind, tuple(parts))


__all__ = ["SEMANTIC_KINDS", "SemanticId", "SemanticKind", "semantic_id"]
