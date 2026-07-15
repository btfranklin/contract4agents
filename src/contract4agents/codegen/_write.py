"""Safe write and freshness checks for generated source files."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath

from contract4agents.codegen._model import GeneratedCode, GeneratedCodeStaleError


def stale_generated_paths(generated: GeneratedCode, output_dir: Path | str) -> tuple[PurePosixPath, ...]:
    """Return generated paths that are missing or differ from expected source."""

    root = Path(output_dir)
    return tuple(
        path
        for path, source in generated.files.items()
        if not (root / Path(*path.parts)).is_file() or (root / Path(*path.parts)).read_text() != source
    )


def write_generated_code(
    generated: GeneratedCode,
    output_dir: Path | str,
    *,
    check: bool = False,
) -> tuple[Path, ...]:
    """Write stale generated files atomically, or fail without writing in check mode."""

    root = Path(output_dir)
    stale = stale_generated_paths(generated, root)
    if check:
        if stale:
            raise GeneratedCodeStaleError(stale)
        return ()

    written: list[Path] = []
    for relative in stale:
        path = root / Path(*relative.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        source = generated.files[relative]
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w") as handle:
                handle.write(source)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        written.append(path)
    return tuple(written)


__all__ = ["stale_generated_paths", "write_generated_code"]
