"""Immutable generated-code artifacts and structured codegen errors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from contract4agents.ir import FrozenMap

GENERATOR_VERSION = "1"
PYDANTIC_MODELS_PATH = PurePosixPath("python/models.py")
TYPESCRIPT_TYPES_PATH = PurePosixPath("typescript/types.ts")
ZOD_SCHEMAS_PATH = PurePosixPath("typescript/schemas.ts")


@dataclass(frozen=True)
class GeneratedCode:
    """One deterministic set of generated source files for a contract digest."""

    contract_digest: str
    files: FrozenMap[PurePosixPath, str]
    generator_version: str = GENERATOR_VERSION

    def __post_init__(self) -> None:
        if not self.contract_digest.startswith("sha256:"):
            raise ValueError("Generated code requires a prefixed contract digest")
        if not self.generator_version:
            raise ValueError("Generated code requires a generator version")
        for path, source in self.files.items():
            if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
                raise ValueError(f"Generated code path must be relative and normalized: {path}")
            if not source.endswith("\n"):
                raise ValueError(f"Generated source `{path}` must end with a newline")


class CodeGenerationError(ValueError):
    """Raised when CanonicalIR cannot be represented by a code generator."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class GeneratedCodeStaleError(CodeGenerationError):
    """Raised by check mode when generated source is missing or stale."""

    def __init__(self, stale_paths: tuple[PurePosixPath, ...]) -> None:
        self.stale_paths = stale_paths
        paths = ", ".join(str(path) for path in stale_paths)
        super().__init__("CGEN002", f"Generated code is stale: {paths}")


__all__ = [
    "GENERATOR_VERSION",
    "PYDANTIC_MODELS_PATH",
    "TYPESCRIPT_TYPES_PATH",
    "ZOD_SCHEMAS_PATH",
    "CodeGenerationError",
    "GeneratedCode",
    "GeneratedCodeStaleError",
]
