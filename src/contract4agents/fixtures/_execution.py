"""Fixture metadata loading and start execution helpers."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, cast

from contract4agents.compiler import CompilerArtifacts
from contract4agents.fixtures._models import FixtureConfigError, RunnerFunc
from contract4agents.runtime._trace import TraceRecorder
from contract4agents.runtime._utils import load_python_ref


def load_fixture_metadata(project_root: Path) -> dict[str, Any]:
    path = project_root / "fixture.json"
    if not path.exists():
        raise FixtureConfigError(f"Contract4Agents fixture metadata missing: {path}")
    return cast(dict[str, Any], json.loads(path.read_text()))


def runner_for_mode(metadata: dict[str, Any], mode: str) -> RunnerFunc:
    if mode == "local":
        return cast(RunnerFunc, load_python_ref(metadata["local_runner"]))
    if mode == "openai":
        if "live_runner" not in metadata:
            raise FixtureConfigError("Fixture mode `openai` requires `live_runner` in fixture.json")
        return cast(RunnerFunc, load_python_ref(metadata["live_runner"]))
    raise FixtureConfigError(f"Unknown fixture mode: {mode}")


def prepare_fixture_import_roots(project_root: Path) -> None:
    candidates = [project_root]
    for parent in project_root.parents:
        if (parent / "pyproject.toml").exists():
            candidates.append(parent)
            break
    for candidate in reversed(candidates):
        path = str(candidate)
        if path not in sys.path:
            sys.path.insert(0, path)


async def run_start_with_retry(
    runner: RunnerFunc,
    start: Any,
    db_path: Path,
    artifacts: CompilerArtifacts,
    trace_path: Path,
    mode: str,
) -> tuple[dict[str, Any], TraceRecorder, int, list[str], Path]:
    retry_errors: list[str] = []
    max_attempts = 2 if mode == "openai" else 1
    for attempt in range(1, max_attempts + 1):
        attempt_trace = (
            trace_path if max_attempts == 1 else trace_path.with_name(f"{trace_path.stem}.attempt-{attempt}.jsonl")
        )
        attempt_trace.parent.mkdir(parents=True, exist_ok=True)
        attempt_db = db_path.parent / "attempts" / f"{trace_path.stem}.attempt-{attempt}.sqlite"
        attempt_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db_path, attempt_db)
        try:
            output, trace = await runner(start, attempt_db, artifacts, attempt_trace)
            return output, trace, attempt, retry_errors, attempt_db
        except Exception as exc:
            if attempt >= max_attempts:
                raise
            retry_errors.append(f"{type(exc).__name__}: {exc}")
    raise RuntimeError("unreachable fixture retry state")


def clean_given(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


__all__ = [
    "clean_given",
    "load_fixture_metadata",
    "prepare_fixture_import_roots",
    "run_start_with_retry",
    "runner_for_mode",
]
