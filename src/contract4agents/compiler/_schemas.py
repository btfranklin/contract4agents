"""Schema and type-binding artifact generation."""

from __future__ import annotations

import hashlib
import json

from contract4agents.ast import ContractProject
from contract4agents.compiler._types import JsonSchema, TypeBinding
from contract4agents.diagnostics import ContractError, Diagnostic
from contract4agents.pydantic_interop import PydanticSchemaError, schema_from_pydantic_type
from contract4agents.schema import type_to_schema


def build_type_artifacts(
    project: ContractProject,
    allow_python_imports: bool = False,
) -> tuple[dict[str, JsonSchema], list[TypeBinding]]:
    schemas: dict[str, JsonSchema] = {}
    bindings: list[TypeBinding] = []
    for name, type_def in project.types.items():
        if type_def.source == "python":
            if not allow_python_imports:
                raise ContractError(
                    [
                        Diagnostic(
                            "PYD000",
                            f"Python imports are disabled for imported type `{name}`",
                            span=type_def.span,
                            hint="Run check or compile with --allow-python-imports to derive schemas from host models.",
                        )
                    ]
                )
            try:
                schema, schema_hash = schema_from_pydantic_type(type_def)
            except PydanticSchemaError as exc:
                raise ContractError([exc.diagnostic]) from exc
        else:
            schema = type_to_schema(type_def, project.types)
            schema_hash = _schema_hash(schema)
        schemas[name] = schema
        bindings.append(
            {
                "type": name,
                "source": type_def.source,
                "python_ref": type_def.python_ref,
                "schema_ref": f"schemas/{name}.json",
                "schema_hash": schema_hash,
            }
        )
    return schemas, bindings


def _schema_hash(schema: JsonSchema) -> str:
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = ["build_type_artifacts"]
