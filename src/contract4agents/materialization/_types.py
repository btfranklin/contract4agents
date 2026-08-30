"""In-memory Pydantic types derived directly from canonical contract types."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, ForwardRef, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, create_model
from pydantic.functional_validators import BeforeValidator

from contract4agents._portable_validation import parse_portable_datetime
from contract4agents.ir import (
    CanonicalIR,
    ConstrainedTypeRef,
    EnumIR,
    FrozenMap,
    ListTypeRef,
    MapTypeRef,
    NamedTypeRef,
    NullableTypeRef,
    ParameterIR,
    PrimitiveTypeRef,
    SemanticId,
    TypeRef,
)
from contract4agents.materialization._errors import MaterializationError, MaterializationIssue


def build_pydantic_types(ir: CanonicalIR) -> FrozenMap[str, Any]:
    built: dict[str, Any] = {}
    type_names = frozenset(item.name for item in ir.types.values())

    def annotation(type_ref: TypeRef) -> Any:
        if isinstance(type_ref, NamedTypeRef):
            name = type_ref.type_id.parts[0]
            if name not in type_names:
                raise MaterializationError(
                    (MaterializationIssue("MAT203", f"Unknown canonical type `{name}`"),)
                )
            return built.get(name, ForwardRef(name))
        if isinstance(type_ref, NullableTypeRef):
            return annotation(type_ref.item) | None
        if isinstance(type_ref, ListTypeRef):
            source = list.__class_getitem__(annotation(type_ref.item))
            if type_ref.min_items is None and type_ref.max_items is None:
                return source
            return Annotated[
                source,
                Field(min_length=type_ref.min_items, max_length=type_ref.max_items),
            ]
        if isinstance(type_ref, MapTypeRef):
            return dict.__class_getitem__((StrictStr, annotation(type_ref.value)))
        return _annotation(type_ref, FrozenMap(built))

    for type_def in ir.types.values():
        if isinstance(type_def, EnumIR):
            enum_type = Literal.__getitem__(type_def.values)
            built[type_def.name] = enum_type

    models: list[type[BaseModel]] = []
    for type_def in ir.types.values():
        if isinstance(type_def, EnumIR):
            continue
        fields: dict[str, tuple[Any, Any]] = {}
        for item in type_def.fields:
            default = _thaw(item.default) if item.has_default else (... if _required(item.type_ref) else None)
            fields[item.name] = (annotation(item.type_ref), Field(default))
        create_model_any = cast(Any, create_model)
        model = cast(
            type[BaseModel],
            create_model_any(
                type_def.name,
                __config__=ConfigDict(extra="forbid", strict=True, allow_inf_nan=False),
                __module__="contract4agents.generated",
                **fields,
            ),
        )
        built[type_def.name] = model
        models.append(model)

    for model in models:
        model.model_rebuild(_types_namespace=built)
    return FrozenMap((name, built[name]) for name in sorted(built))


def build_parameter_model(
    name: str,
    parameters: tuple[ParameterIR, ...],
    output_types: FrozenMap[str, Any],
) -> type[object] | None:
    if not parameters:
        return None
    fields: dict[str, tuple[Any, Any]] = {}
    for parameter in parameters:
        default = _thaw(parameter.default) if parameter.has_default else (... if parameter.required else None)
        fields[parameter.name] = (_annotation(parameter.type_ref, output_types), Field(default))
    create_model_any = cast(Any, create_model)
    return cast(
        type[object],
        create_model_any(
            name,
            __config__=ConfigDict(extra="forbid", strict=True, allow_inf_nan=False),
            __module__="contract4agents.generated",
            **fields,
        ),
    )


def build_agent_input_types(
    ir: CanonicalIR,
    output_types: FrozenMap[str, Any],
) -> FrozenMap[SemanticId, type[object] | None]:
    """Build one strict invocation-input type for each contract agent."""

    return FrozenMap(
        (
            agent_id,
            build_parameter_model(
                f"{agent.name}Input",
                agent.parameters,
                output_types,
            ),
        )
        for agent_id, agent in ir.agents.items()
    )


def output_type_for(type_ref: TypeRef, output_types: FrozenMap[str, Any]) -> Any:
    if isinstance(type_ref, NamedTypeRef):
        return output_types[type_ref.type_id.parts[0]]
    raise MaterializationError(
        (MaterializationIssue("MAT204", "Agent output must resolve to a named contract type"),)
    )


def type_adapter_for(type_ref: TypeRef, output_types: FrozenMap[str, Any]) -> Any:
    """Return a Pydantic adapter for any portable contract type reference."""

    from pydantic import TypeAdapter

    return TypeAdapter(_annotation(type_ref, output_types))


def normalize_structural_value(value: object) -> object:
    """Convert application Pydantic models to ordinary structural data."""

    if isinstance(value, BaseModel):
        return normalize_structural_value(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {key: normalize_structural_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [normalize_structural_value(child) for child in value]
    return value


def _annotation(type_ref: TypeRef, output_types: FrozenMap[str, Any]) -> Any:
    if isinstance(type_ref, PrimitiveTypeRef):
        return {
            "string": StrictStr,
            "integer": StrictInt,
            "float": Annotated[StrictFloat, Field(allow_inf_nan=False)],
            "boolean": StrictBool,
            "datetime": Annotated[datetime, BeforeValidator(parse_portable_datetime)],
        }[type_ref.name]
    if isinstance(type_ref, ConstrainedTypeRef):
        metadata = Field(
            ge=type_ref.minimum,
            le=type_ref.maximum,
            min_length=type_ref.min_length,
            max_length=type_ref.max_length,
        )
        return Annotated[_annotation(type_ref.item, output_types), metadata]
    if isinstance(type_ref, NamedTypeRef):
        return output_types[type_ref.type_id.parts[0]]
    if isinstance(type_ref, NullableTypeRef):
        return _annotation(type_ref.item, output_types) | None
    if isinstance(type_ref, ListTypeRef):
        source = list.__class_getitem__(_annotation(type_ref.item, output_types))
        metadata = Field(min_length=type_ref.min_items, max_length=type_ref.max_items)
        if type_ref.min_items is None and type_ref.max_items is None:
            return source
        return Annotated[source, metadata]
    if isinstance(type_ref, MapTypeRef):
        return dict.__class_getitem__((StrictStr, _annotation(type_ref.value, output_types)))
    raise TypeError(f"Unsupported type reference {type(type_ref).__name__}")


def _required(type_ref: TypeRef) -> bool:
    return not isinstance(type_ref, NullableTypeRef)


def _thaw(value: object) -> object:
    if isinstance(value, FrozenMap):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


__all__ = [
    "build_agent_input_types",
    "build_parameter_model",
    "build_pydantic_types",
    "normalize_structural_value",
    "output_type_for",
    "type_adapter_for",
]
