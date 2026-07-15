"""Structured provider-neutral planning failures."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents.ir import SemanticId


@dataclass(frozen=True)
class PlanningIssue:
    code: str
    message: str
    semantic_id: SemanticId | None = None

    def format(self) -> str:
        suffix = f" [{self.semantic_id}]" if self.semantic_id is not None else ""
        return f"{self.code}: {self.message}{suffix}"


class PlanningError(ValueError):
    """One or more target-resolution requirements could not be satisfied."""

    def __init__(self, issues: tuple[PlanningIssue, ...]) -> None:
        if not issues:
            raise ValueError("PlanningError requires at least one issue")
        self.issues = issues
        super().__init__("\n".join(issue.format() for issue in issues))


__all__ = ["PlanningError", "PlanningIssue"]
