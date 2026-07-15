"""Portable source generation from Contract4Agents CanonicalIR."""

from contract4agents.codegen._generate import (
    generate_code,
    generate_pydantic_models,
    generate_typescript_types,
    generate_zod_schemas,
)
from contract4agents.codegen._model import (
    GENERATOR_VERSION,
    PYDANTIC_MODELS_PATH,
    TYPESCRIPT_TYPES_PATH,
    ZOD_SCHEMAS_PATH,
    CodeGenerationError,
    GeneratedCode,
    GeneratedCodeStaleError,
)
from contract4agents.codegen._write import stale_generated_paths, write_generated_code

__all__ = [
    "GENERATOR_VERSION",
    "PYDANTIC_MODELS_PATH",
    "TYPESCRIPT_TYPES_PATH",
    "ZOD_SCHEMAS_PATH",
    "CodeGenerationError",
    "GeneratedCode",
    "GeneratedCodeStaleError",
    "generate_code",
    "generate_pydantic_models",
    "generate_typescript_types",
    "generate_zod_schemas",
    "stale_generated_paths",
    "write_generated_code",
]
