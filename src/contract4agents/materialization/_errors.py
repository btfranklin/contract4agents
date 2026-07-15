"""Structured failures at the materialization boundary."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents.ir import SemanticId


@dataclass(frozen=True)
class MaterializationIssue:
    code: str
    message: str
    semantic_id: SemanticId | None = None

    def format(self) -> str:
        suffix = f" [{self.semantic_id}]" if self.semantic_id is not None else ""
        return f"{self.code}: {self.message}{suffix}"


class MaterializationError(Exception):
    """Raised before a partial native graph can escape."""

    def __init__(self, issues: tuple[MaterializationIssue, ...]) -> None:
        if not issues:
            raise ValueError("MaterializationError requires at least one issue")
        self.issues = issues
        super().__init__("\n".join(issue.format() for issue in issues))


__all__ = ["MaterializationError", "MaterializationIssue"]
