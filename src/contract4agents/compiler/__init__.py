"""Canonical provider-neutral compiler for Contract4Agents projects."""

from contract4agents.compiler._v2 import (
    CompilerArtifacts,
    artifact_digests,
    build_artifacts,
    compile_project,
)
from contract4agents.compiler._v2_writer import V2_MANAGED_ARTIFACT_DIRS, write_artifacts

__all__ = [
    "CompilerArtifacts",
    "V2_MANAGED_ARTIFACT_DIRS",
    "artifact_digests",
    "build_artifacts",
    "compile_project",
    "write_artifacts",
]
