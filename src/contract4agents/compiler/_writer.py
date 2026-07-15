"""Atomic artifact writing and freshness checking for the compiler."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from contract4agents.compiler._compiler import CompilerArtifacts
from contract4agents.diagnostics import ContractError, Diagnostic
from contract4agents.ir import canonical_ir_data

MANAGED_ARTIFACT_DIRS = ("ir", "schemas", "instructions", "generated", "docs")


def write_artifacts(artifacts: CompilerArtifacts, output_dir: Path, *, check: bool = False) -> None:
    files = _artifact_files(artifacts, output_dir)
    if check:
        expected = set(files)
        stale = [path for path, source in files.items() if not path.is_file() or path.read_text() != source]
        stale.extend(
            path
            for directory in MANAGED_ARTIFACT_DIRS
            for path in sorted((output_dir / directory).rglob("*"))
            if path.is_file() and path not in expected
        )
        if stale:
            raise ContractError(
                [
                    Diagnostic(
                        "COMPILE001",
                        "Generated artifacts are stale",
                        hint="Rerun compile without --check.\nStale files:\n"
                        + "\n".join(str(path) for path in stale[:10]),
                    )
                ]
            )
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix=".contract4agents-", dir=output_dir))
    try:
        for path, source in files.items():
            temporary = temporary_root / path.relative_to(output_dir)
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(source)
        for directory in MANAGED_ARTIFACT_DIRS:
            current = output_dir / directory
            replacement = temporary_root / directory
            if current.exists():
                shutil.rmtree(current)
            if replacement.exists():
                shutil.move(str(replacement), str(current))
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def _artifact_files(artifacts: CompilerArtifacts, output_dir: Path) -> dict[Path, str]:
    files = {
        output_dir / "ir" / "contract.json": _json(canonical_ir_data(artifacts.ir)),
        output_dir / "ir" / "contract-digest.txt": artifacts.contract_digest + "\n",
    }
    for name, schema in artifacts.schemas.items():
        files[output_dir / "schemas" / f"{name}.json"] = _json(schema)
    for name, instructions in artifacts.instructions.items():
        files[output_dir / "instructions" / f"{name}.md"] = instructions
    for path, source in artifacts.generated_code.files.items():
        files[output_dir / "generated" / Path(*path.parts)] = source
    for path, source in artifacts.docs.items():
        files[output_dir / "docs" / Path(*path.parts)] = source
    return files


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


__all__ = ["MANAGED_ARTIFACT_DIRS", "write_artifacts"]
