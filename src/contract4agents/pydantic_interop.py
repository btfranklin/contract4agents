"""Pydantic model import and schema derivation helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel

from contract4agents.ast import TypeDef
from contract4agents.diagnostics import Diagnostic
from contract4agents.runtime import load_python_ref


def is_python_import_ref(value: str | None) -> bool:
    return bool(value and ":" in value and all(part.strip() for part in value.split(":", 1)))


def python_type_ref_diagnostics(type_def: TypeDef) -> list[Diagnostic]:
    if type_def.source != "python":
        return []
    diagnostics: list[Diagnostic] = []
    if type_def.fields:
        diagnostics.append(
            Diagnostic(
                "PYD001",
                f"Imported Python type `{type_def.name}` cannot declare native fields",
                span=type_def.span,
            )
        )
    if not is_python_import_ref(type_def.python_ref):
        diagnostics.append(
            Diagnostic(
                "PYD002",
                f"Python type `{type_def.name}` must use an import path of the form `module:object`",
                span=type_def.span,
            )
        )
    return diagnostics


def schema_from_pydantic_type(type_def: TypeDef) -> tuple[dict[str, Any], str]:
    if not type_def.python_ref:
        raise _schema_error(type_def, "PYD002", f"Python type `{type_def.name}` is missing an import path")
    try:
        model = load_python_ref(type_def.python_ref)
    except Exception as exc:
        raise _schema_error(
            type_def,
            "PYD010",
            f"Could not import Python model for `{type_def.name}` from `{type_def.python_ref}`",
            hint=str(exc),
        ) from exc
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise _schema_error(
            type_def,
            "PYD011",
            f"Python type `{type_def.name}` must reference a Pydantic v2 BaseModel class",
        )
    if not hasattr(model, "model_json_schema"):
        raise _schema_error(
            type_def,
            "PYD012",
            f"Python type `{type_def.name}` does not expose Pydantic v2 `model_json_schema()`",
        )
    try:
        schema = model.model_json_schema(ref_template="#/$defs/{model}")
    except Exception as exc:
        raise _schema_error(
            type_def,
            "PYD013",
            f"Could not derive JSON Schema from `{type_def.python_ref}`",
            hint=str(exc),
        ) from exc
    if not isinstance(schema, dict):
        raise _schema_error(type_def, "PYD014", f"Python type `{type_def.name}` produced a non-object schema")
    if schema.get("type") != "object":
        raise _schema_error(
            type_def,
            "PYD015",
            f"Python type `{type_def.name}` must produce a top-level object schema",
        )
    schema = dict(schema)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = type_def.name
    try:
        encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    except TypeError as exc:
        raise _schema_error(
            type_def,
            "PYD016",
            f"Python type `{type_def.name}` produced a schema that is not JSON serializable",
            hint=str(exc),
        ) from exc
    return schema, hashlib.sha256(encoded.encode()).hexdigest()


class PydanticSchemaError(Exception):
    def __init__(self, diagnostic: Diagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.format())


def _schema_error(
    type_def: TypeDef,
    code: str,
    message: str,
    *,
    hint: str | None = None,
) -> PydanticSchemaError:
    return PydanticSchemaError(Diagnostic(code, message, span=type_def.span, hint=hint))


__all__ = [
    "PydanticSchemaError",
    "is_python_import_ref",
    "python_type_ref_diagnostics",
    "schema_from_pydantic_type",
]
