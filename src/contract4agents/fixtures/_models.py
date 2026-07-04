"""Fixture runner report models and errors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from contract4agents.compiler import CompilerArtifacts
from contract4agents.runtime._trace import TraceRecorder

GENERATED_ARTIFACT_DIRS = ("build", "data", "traces")


class FixtureConfigError(RuntimeError):
    pass


class FixtureArtifactError(AssertionError):
    pass


class FixtureRetryError(RuntimeError):
    def __init__(self, message: str, *, attempts: int, retry_errors: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.retry_errors = retry_errors


@dataclass
class StartReport:
    start_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    assertion_failures: list[str] = field(default_factory=list)
    skipped_semantic: list[str] = field(default_factory=list)
    monitor_violations: list[str] = field(default_factory=list)
    attempts: int = 1
    retry_errors: list[str] = field(default_factory=list)


@dataclass
class FixtureReport:
    project: str
    mode: str
    artifact_checks: list[str]
    starts: list[StartReport]
    cleaned: bool
    run_root: str

    @property
    def passed(self) -> bool:
        return all(item.passed and not item.monitor_violations for item in self.starts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "mode": self.mode,
            "artifact_checks": self.artifact_checks,
            "starts": [asdict(item) for item in self.starts],
            "cleaned": self.cleaned,
            "run_root": self.run_root,
            "summary": {
                "passed": self.passed,
                "start_count": len(self.starts),
                "failures": sum(len(item.failures) for item in self.starts),
                "assertion_failures": sum(len(item.assertion_failures) for item in self.starts),
                "monitor_violations": sum(len(item.monitor_violations) for item in self.starts),
            },
        }


RunnerFunc = Callable[[Any, Path, CompilerArtifacts, Path], Awaitable[tuple[dict[str, Any], TraceRecorder]]]


__all__ = [
    "FixtureArtifactError",
    "FixtureConfigError",
    "FixtureRetryError",
    "FixtureReport",
    "GENERATED_ARTIFACT_DIRS",
    "RunnerFunc",
    "StartReport",
]
