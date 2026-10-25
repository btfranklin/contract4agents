"""In-memory Pydantic types derived directly from canonical contract types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, ForwardRef, TypeAlias

from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, StrictStr, TypeAdapter
from pydantic.fields import FieldInfo
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
from contract4agents.materialization._annotations import (
    constrained_annotation,
    contract_model,
    enum_annotation,
    field_info,
    list_annotation,
    map_annotation,
    nullable_annotation,
)
from contract4agents.materialization._errors import MaterializationError, MaterializationIssue


@dataclass(frozen=True)
class GeneratedEnum:
    """A generated enum validator; an enum annotation is not a Python class."""

    values: tuple[str, ...]

    @property
    def annotation(self) -> object:
        return enum_annotation(self.values)

    @property
    def adapter(self) -> TypeAdapter[str]:
        return TypeAdapter[str](self.annotation)


GeneratedType: TypeAlias = type[BaseModel] | GeneratedEnum


def annotation_name(annotation: object) -> str:
    return annotation.__qualname__ if isinstance(annotation, type) else str(annotation)


def _runtime_annotation(value: GeneratedType) -> object:
    return value.annotation if isinstance(value, GeneratedEnum) else value


def build_pydantic_types(ir: CanonicalIR) -> FrozenMap[str, GeneratedType]:
    built: dict[str, GeneratedType] = {}
    type_names = frozenset(item.name for item in ir.types.values())

    def annotation(type_ref: TypeRef) -> object:
        if isinstance(type_ref, NamedTypeRef):
            name = type_ref.type_id.parts[0]
            if name not in type_names:
                raise MaterializationError(
                    (MaterializationIssue("MAT203", f"Unknown canonical type `{name}`"),)
                )
            return _runtime_annotation(built[name]) if name in built else ForwardRef(name)
        if isinstance(type_ref, NullableTypeRef):
            return nullable_annotation(annotation(type_ref.item))
        if isinstance(type_ref, ListTypeRef):
            source = list_annotation(annotation(type_ref.item))
            if type_ref.min_items is None and type_ref.max_items is None:
                return source
            return constrained_annotation(source, Field(min_length=type_ref.min_items, max_length=type_ref.max_items))
        if isinstance(type_ref, MapTypeRef):
            return map_annotation(StrictStr, annotation(type_ref.value))
        return _annotation(type_ref, FrozenMap(built))

    for type_def in ir.types.values():
        if isinstance(type_def, EnumIR):
            built[type_def.name] = GeneratedEnum(type_def.values)

    models: list[type[BaseModel]] = []
    for type_def in ir.types.values():
        if isinstance(type_def, EnumIR):
            continue
        fields: dict[str, tuple[object, FieldInfo]] = {}
        for item in type_def.fields:
            default = _thaw(item.default) if item.has_default else (... if _required(item.type_ref) else None)
            fields[item.name] = (annotation(item.type_ref), field_info(default))
        model = contract_model(type_def.name, fields)
        built[type_def.name] = model
        models.append(model)

    for model in models:
        model.model_rebuild(_types_namespace={name: _runtime_annotation(value) for name, value in built.items()})
    return FrozenMap((name, built[name]) for name in sorted(built))


def build_parameter_model(
    name: str,
    parameters: tuple[ParameterIR, ...],
    output_types: FrozenMap[str, GeneratedType],
) -> type[BaseModel] | None:
    if not parameters:
        return None
    fields: dict[str, tuple[object, FieldInfo]] = {}
    for parameter in parameters:
        default = _thaw(parameter.default) if parameter.has_default else (... if parameter.required else None)
        fields[parameter.name] = (_annotation(parameter.type_ref, output_types), field_info(default))
    return contract_model(name, fields)


def build_agent_input_types(
    ir: CanonicalIR,
    output_types: FrozenMap[str, GeneratedType],
) -> FrozenMap[SemanticId, type[BaseModel] | None]:
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


def output_type_for(type_ref: TypeRef, output_types: FrozenMap[str, GeneratedType]) -> object:
    if isinstance(type_ref, NamedTypeRef):
        return _runtime_annotation(output_types[type_ref.type_id.parts[0]])
    raise MaterializationError(
        (MaterializationIssue("MAT204", "Agent output must resolve to a named contract type"),)
    )


def type_adapter_for(type_ref: TypeRef, output_types: FrozenMap[str, GeneratedType]) -> TypeAdapter[object]:
    """Return a Pydantic adapter for any portable contract type reference."""

    return TypeAdapter[object](_annotation(type_ref, output_types))


def normalize_structural_value(value: object) -> object:
    """Convert application Pydantic models to ordinary structural data."""

    if isinstance(value, BaseModel):
        return normalize_structural_value(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {key: normalize_structural_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [normalize_structural_value(child) for child in value]
    return value


def _annotation(type_ref: TypeRef, output_types: FrozenMap[str, GeneratedType]) -> object:
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
        return constrained_annotation(_annotation(type_ref.item, output_types), metadata)
    if isinstance(type_ref, NamedTypeRef):
        return _runtime_annotation(output_types[type_ref.type_id.parts[0]])
    if isinstance(type_ref, NullableTypeRef):
        return nullable_annotation(_annotation(type_ref.item, output_types))
    if isinstance(type_ref, ListTypeRef):
        source = list_annotation(_annotation(type_ref.item, output_types))
        metadata = Field(min_length=type_ref.min_items, max_length=type_ref.max_items)
        if type_ref.min_items is None and type_ref.max_items is None:
            return source
        return constrained_annotation(source, metadata)
    if isinstance(type_ref, MapTypeRef):
        return map_annotation(StrictStr, _annotation(type_ref.value, output_types))
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
    "GeneratedEnum",
    "GeneratedType",
    "build_agent_input_types",
    "build_parameter_model",
    "build_pydantic_types",
    "normalize_structural_value",
    "output_type_for",
    "type_adapter_for",
]
