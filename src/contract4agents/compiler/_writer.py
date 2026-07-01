"""Artifact writing and freshness checks."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from contract4agents.compiler._types import MANAGED_ARTIFACT_DIRS, CompilerArtifacts
from contract4agents.diagnostics import ContractError, Diagnostic


def write_artifacts(artifacts: CompilerArtifacts, output_dir: Path, check: bool = False) -> None:
    files: dict[Path, str] = {}
    for name, schema in artifacts["schemas"].items():
        files[output_dir / "schemas" / f"{name}.json"] = _json(schema)
    files[output_dir / "types" / "type-bindings.json"] = _json(artifacts["type_bindings"])
    for name, manifest in artifacts["manifests"].items():
        files[output_dir / "manifests" / f"{name}.json"] = _json(manifest)
    for name, instructions in artifacts["instructions"].items():
        files[output_dir / "instructions" / f"{name}.md"] = instructions
    files[output_dir / "evals" / "evals.json"] = _json(artifacts["evals"])
    files[output_dir / "monitors" / "monitors.json"] = _json(artifacts["monitors"])
    files[output_dir / "guards" / "guard-plan.json"] = _json(artifacts["guard_plan"])
    files[output_dir / "adapters" / "capability-matrix.json"] = _json(artifacts["adapter_capability_matrix"])
    for name, text in artifacts["docs"].items():
        files[output_dir / "docs" / name] = text
    if check:
        expected_paths = set(files)
        stale = [path for path, content in files.items() if not path.exists() or path.read_text() != content]
        extra = [
            path
            for dirname in MANAGED_ARTIFACT_DIRS
            for path in sorted((output_dir / dirname).rglob("*"))
            if path.is_file() and path not in expected_paths
        ]
        stale.extend(extra)
        if stale:
            raise ContractError(
                [
                    Diagnostic(
                        "COMPILE001",
                        "Generated artifacts are stale",
                        hint="Rerun compile without --check to refresh generated artifacts.\n"
                        + "Stale files:\n"
                        + "\n".join(str(path) for path in stale[:10]),
                    )
                ]
            )
        return
    for dirname in MANAGED_ARTIFACT_DIRS:
        managed_dir = output_dir / dirname
        if managed_dir.exists():
            shutil.rmtree(managed_dir)
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


__all__ = ["write_artifacts"]
