"""Generated-output path validation."""

from __future__ import annotations

from pathlib import Path

from contract4agents.diagnostics import ContractError, Diagnostic

SOURCE_OWNED_OUTPUT_DIRS = frozenset(
    {
        "agents",
        "datasources",
        "docs",
        "evals",
        "examples",
        "monitors",
        "src",
        "tests",
        "types",
    }
)


def validate_output_dir(root: Path | str, output_dir: Path | str, *, artifact_label: str = "generated output") -> Path:
    """Resolve and validate a generated-output directory.

    Relative output paths are intentionally resolved from the caller's current
    working directory, not from the contract root.
    """
    root_path = Path(root).resolve()
    cwd_path = Path.cwd().resolve()
    raw_output = Path(output_dir)
    output_path = raw_output if raw_output.is_absolute() else cwd_path / raw_output
    output_path = output_path.resolve()

    for anchor, label in ((root_path, "project root"), (cwd_path, "current working directory")):
        if output_path == anchor:
            _raise_unsafe_output_dir(output_dir, artifact_label, f"it is the {label}")
        for dirname in SOURCE_OWNED_OUTPUT_DIRS:
            source_dir = anchor / dirname
            if output_path == source_dir or output_path.is_relative_to(source_dir):
                _raise_unsafe_output_dir(
                    output_dir,
                    artifact_label,
                    f"`{output_path}` is inside source-owned directory `{source_dir}`",
                )
    return output_path


def _raise_unsafe_output_dir(output_dir: Path | str, artifact_label: str, reason: str) -> None:
    raise ContractError(
        [
            Diagnostic(
                "COMPILE002",
                f"Refusing to write {artifact_label} to `{output_dir}` because {reason}.",
                hint="Choose a generated artifact directory such as `.contract/build`.",
            )
        ]
    )


__all__ = ["SOURCE_OWNED_OUTPUT_DIRS", "validate_output_dir"]
