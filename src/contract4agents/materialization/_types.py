"""In-memory Pydantic types derived directly from canonical contract types."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from contract4agents.ir import (
    CanonicalIR,
    FrozenMap,
    ListTypeRef,
    MapTypeRef,
    NamedTypeRef,
    NullableTypeRef,
    ParameterIR,
    PrimitiveTypeRef,
    TypeRef,
)
from contract4agents.materialization._errors import MaterializationError, MaterializationIssue


def build_pydantic_types(ir: CanonicalIR) -> FrozenMap[str, type[object]]:
    try:
        from pydantic import ConfigDict, Field, create_model
    except Exception as exc:  # noqa: BLE001 - optional provider boundary.
        raise MaterializationError(
            (MaterializationIssue("MAT201", "Pydantic v2 is required for native output types"),)
        ) from exc

    built: dict[str, type[object]] = {}
    resolving: set[str] = set()

    def annotation(type_ref: TypeRef) -> Any:
        if isinstance(type_ref, NamedTypeRef):
            return build(type_ref.type_id.parts[0])
        if isinstance(type_ref, NullableTypeRef):
            return annotation(type_ref.item) | None
        if isinstance(type_ref, ListTypeRef):
            return list.__class_getitem__(annotation(type_ref.item))
        if isinstance(type_ref, MapTypeRef):
            return dict.__class_getitem__((str, annotation(type_ref.value)))
        return _annotation(type_ref, FrozenMap(built))

    def build(name: str) -> type[object]:
        if name in built:
            return built[name]
        if name in resolving:
            raise MaterializationError(
                (MaterializationIssue("MAT202", f"Recursive type `{name}` cannot be materialized"),)
            )
        type_def = next((item for item in ir.types.values() if item.name == name), None)
        if type_def is None:
            raise MaterializationError(
                (MaterializationIssue("MAT203", f"Unknown canonical type `{name}`"),)
            )
        resolving.add(name)
        fields: dict[str, tuple[Any, Any]] = {}
        for item in type_def.fields:
            default = _thaw(item.default) if item.has_default else (... if _required(item.type_ref) else None)
            fields[item.name] = (annotation(item.type_ref), Field(default))
        create_model_any = cast(Any, create_model)
        model = cast(
            type[object],
            create_model_any(
                name,
                __config__=ConfigDict(extra="forbid"),
                __module__="contract4agents.generated",
                **fields,
            ),
        )
        resolving.remove(name)
        built[name] = model
        return model

    for type_def in ir.types.values():
        build(type_def.name)
    return FrozenMap((name, built[name]) for name in sorted(built))


def build_parameter_model(
    name: str,
    parameters: tuple[ParameterIR, ...],
    output_types: FrozenMap[str, type[object]],
) -> type[object] | None:
    if not parameters:
        return None
    from pydantic import ConfigDict, Field, create_model

    fields: dict[str, tuple[Any, Any]] = {}
    for parameter in parameters:
        default = _thaw(parameter.default) if parameter.has_default else (... if parameter.required else None)
        fields[parameter.name] = (_annotation(parameter.type_ref, output_types), Field(default))
    create_model_any = cast(Any, create_model)
    return cast(
        type[object],
        create_model_any(
            name,
            __config__=ConfigDict(extra="forbid"),
            __module__="contract4agents.generated",
            **fields,
        ),
    )


def output_type_for(type_ref: TypeRef, output_types: FrozenMap[str, type[object]]) -> type[object]:
    if isinstance(type_ref, NamedTypeRef):
        return output_types[type_ref.type_id.parts[0]]
    raise MaterializationError(
        (MaterializationIssue("MAT204", "Agent output must resolve to a named contract type"),)
    )


def type_adapter_for(type_ref: TypeRef, output_types: FrozenMap[str, type[object]]) -> Any:
    """Return a Pydantic adapter for any portable contract type reference."""

    from pydantic import TypeAdapter

    return TypeAdapter(_annotation(type_ref, output_types))


def _annotation(type_ref: TypeRef, output_types: FrozenMap[str, type[object]]) -> Any:
    if isinstance(type_ref, PrimitiveTypeRef):
        return {
            "string": str,
            "integer": int,
            "float": float,
            "boolean": bool,
            "datetime": datetime,
        }[type_ref.name]
    if isinstance(type_ref, NamedTypeRef):
        return output_types[type_ref.type_id.parts[0]]
    if isinstance(type_ref, NullableTypeRef):
        return _annotation(type_ref.item, output_types) | None
    if isinstance(type_ref, ListTypeRef):
        return list.__class_getitem__(_annotation(type_ref.item, output_types))
    if isinstance(type_ref, MapTypeRef):
        return dict.__class_getitem__((str, _annotation(type_ref.value, output_types)))
    raise TypeError(f"Unsupported type reference {type(type_ref).__name__}")


def _required(type_ref: TypeRef) -> bool:
    return not isinstance(type_ref, NullableTypeRef)


def _thaw(value: object) -> object:
    if isinstance(value, FrozenMap):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


__all__ = ["build_parameter_model", "build_pydantic_types", "output_type_for", "type_adapter_for"]
