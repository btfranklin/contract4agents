"""Deterministic Pydantic, TypeScript, and Zod generation from CanonicalIR."""

from __future__ import annotations

import json
import keyword
import re
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath

from contract4agents.codegen._model import (
    GENERATION_TARGETS,
    GENERATOR_VERSION,
    PYDANTIC_MODELS_PATH,
    TYPESCRIPT_TYPES_PATH,
    ZOD_SCHEMAS_PATH,
    CodeGenerationError,
    GeneratedCode,
)
from contract4agents.ir import (
    CanonicalIR,
    ConstrainedTypeRef,
    EnumIR,
    FrozenJsonValue,
    FrozenMap,
    ListTypeRef,
    MapTypeRef,
    NamedTypeRef,
    NullableTypeRef,
    PrimitiveTypeRef,
    SemanticId,
    TypeDeclarationIR,
    TypeIR,
    TypeRef,
    contract_digest,
)

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def generate_code(ir: CanonicalIR, *, targets: Iterable[str]) -> GeneratedCode:
    """Generate the selected portable language artifacts from canonical IR."""

    requested = frozenset(targets)
    if not requested:
        raise CodeGenerationError("CGEN003", "At least one generation target is required")
    unknown = requested.difference(GENERATION_TARGETS)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise CodeGenerationError("CGEN003", f"Unknown generation target: {names}")
    digest = contract_digest(ir)
    ordered_types = _ordered_types(ir)
    files: list[tuple[PurePosixPath, str]] = []
    if "python" in requested:
        files.append((PYDANTIC_MODELS_PATH, generate_pydantic_models(ir, ordered_types=ordered_types)))
    if "typescript" in requested:
        files.extend(
            (
                (TYPESCRIPT_TYPES_PATH, generate_typescript_types(ir, ordered_types=ordered_types)),
                (ZOD_SCHEMAS_PATH, generate_zod_schemas(ir, ordered_types=ordered_types)),
            )
        )
    return GeneratedCode(contract_digest=digest, files=FrozenMap(files))


def generate_pydantic_models(
    ir: CanonicalIR,
    *,
    ordered_types: tuple[TypeDeclarationIR, ...] | None = None,
) -> str:
    """Generate a self-contained Pydantic v2 model module."""

    types = ordered_types if ordered_types is not None else _ordered_types(ir)
    digest = contract_digest(ir)
    lines = [
        *_header("#", digest),
        "from __future__ import annotations",
        "",
    ]
    if _uses_primitive(types, "datetime"):
        lines.extend(
            [
                "import re",
                "from datetime import datetime",
                "",
            ]
        )
    typing_imports: list[str] = []
    if any(isinstance(type_def, EnumIR) for type_def in types):
        typing_imports.append("Literal")
    if _uses_constraints(types) or _uses_primitive(types, "datetime"):
        typing_imports.append("Annotated")
    if typing_imports:
        lines.extend([f"from typing import {', '.join(sorted(typing_imports))}", ""])
    pydantic_imports = ["BaseModel", "ConfigDict"]
    if _uses_constraints(types):
        pydantic_imports.append("Field")
    if _uses_primitive(types, "datetime"):
        pydantic_imports.append("BeforeValidator")
    lines.extend([f"from pydantic import {', '.join(sorted(pydantic_imports))}", ""])
    if _uses_primitive(types, "datetime"):
        lines.extend(_pydantic_datetime_helper())
        lines.append("")

    for type_def in types:
        if isinstance(type_def, EnumIR):
            values = ", ".join(repr(value) for value in type_def.values)
            lines.append(f"{type_def.name} = Literal[{values}]")
        else:
            lines.extend(_pydantic_model(type_def))
        lines.append("")
    models = tuple(type_def for type_def in types if isinstance(type_def, TypeIR))
    if models:
        model_names = ", ".join(type_def.name for type_def in models)
        if len(models) == 1:
            model_names += ","
        lines.extend(
            [
                f"for _model in ({model_names}):",
                "    _model.model_rebuild()",
                "del _model",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def generate_typescript_types(
    ir: CanonicalIR,
    *,
    ordered_types: tuple[TypeDeclarationIR, ...] | None = None,
) -> str:
    """Generate TypeScript interfaces for serialized contract values."""

    types = ordered_types if ordered_types is not None else _ordered_types(ir)
    lines = [*_header("//", contract_digest(ir))]
    for type_def in types:
        if isinstance(type_def, EnumIR):
            values = " | ".join(json.dumps(value, ensure_ascii=False) for value in type_def.values)
            lines.extend([f"export type {type_def.name} = {values};", ""])
            continue
        lines.extend([f"export interface {type_def.name} {{"])
        for type_field in type_def.fields:
            optional = "?" if isinstance(type_field.type_ref, NullableTypeRef) and not type_field.has_default else ""
            lines.append(f"  {type_field.name}{optional}: {_typescript_type(type_field.type_ref)};")
        lines.extend(["}", ""])
    return "\n".join(lines).rstrip() + "\n"


def generate_zod_schemas(
    ir: CanonicalIR,
    *,
    ordered_types: tuple[TypeDeclarationIR, ...] | None = None,
) -> str:
    """Generate forward-reference-safe Zod schemas for serialized contract values."""

    types = ordered_types if ordered_types is not None else _ordered_types(ir)
    lines = [*_header("//", contract_digest(ir)), 'import { z } from "zod";']
    if types:
        names = ", ".join(type_def.name for type_def in types)
        lines.append(f'import type {{ {names} }} from "./types";')
    lines.append("")
    if _uses_primitive(types, "datetime"):
        lines.extend(_zod_datetime_helper())
        lines.append("")
    for type_def in types:
        if isinstance(type_def, EnumIR):
            values = ", ".join(json.dumps(value, ensure_ascii=False) for value in type_def.values)
            lines.extend(
                [f"export const {type_def.name}Schema: z.ZodType<{type_def.name}> = z.enum([{values}]);", ""]
            )
            continue
        lines.extend([f"export const {type_def.name}Schema: z.ZodType<{type_def.name}> = z.lazy(() =>"])
        lines.append("  z")
        lines.append("    .object({")
        for type_field in type_def.fields:
            schema = _zod_type(type_field.type_ref)
            if type_field.has_default:
                schema = f"{schema}.default({_typescript_literal(type_field.default)})"
            elif isinstance(type_field.type_ref, NullableTypeRef):
                schema = f"{schema}.optional().default(null)"
            lines.append(f"      {type_field.name}: {schema},")
        lines.extend(["    })", "    .strict(),", ");", ""])
    return "\n".join(lines).rstrip() + "\n"


def _pydantic_datetime_helper() -> list[str]:
    return [
        "_PORTABLE_DATETIME_PATTERN = re.compile(",
        '    r"\\A(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"',
        '    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"',
        '    r"(?:\\.[0-9]+)?(?:Z|[+-](?P<offset_hour>[0-9]{2}):(?P<offset_minute>[0-9]{2}))\\Z"',
        ")",
        '_PORTABLE_DATETIME_ERROR = "Expected RFC 3339 datetime with a required Z or ±HH:MM offset"',
        "",
        "def parse_portable_datetime(value: object) -> datetime:",
        '    """Parse one portable RFC 3339 datetime and return an aware datetime."""',
        "",
        "    if isinstance(value, datetime):",
        "        if value.tzinfo is None or value.utcoffset() is None:",
        "            raise ValueError(_PORTABLE_DATETIME_ERROR)",
        "        return value",
        "    if not isinstance(value, str):",
        "        raise ValueError(_PORTABLE_DATETIME_ERROR)",
        "    match = _PORTABLE_DATETIME_PATTERN.fullmatch(value)",
        "    if match is None:",
        "        raise ValueError(_PORTABLE_DATETIME_ERROR)",
        "    parts = match.groupdict()",
        "    year = int(parts[\"year\"])",
        "    month = int(parts[\"month\"])",
        "    day = int(parts[\"day\"])",
        "    hour = int(parts[\"hour\"])",
        "    minute = int(parts[\"minute\"])",
        "    second = int(parts[\"second\"])",
        "    offset_hour = int(parts[\"offset_hour\"] or 0)",
        "    offset_minute = int(parts[\"offset_minute\"] or 0)",
        "    if (",
        "        year == 0",
        "        or month < 1",
        "        or month > 12",
        "        or day < 1",
        "        or hour > 23",
        "        or minute > 59",
        "        or second > 59",
        "        or offset_hour > 23",
        "        or offset_minute > 59",
        "    ):",
        "        raise ValueError(_PORTABLE_DATETIME_ERROR)",
        "    try:",
        '        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))',
        "    except ValueError as exc:",
        "        raise ValueError(_PORTABLE_DATETIME_ERROR) from exc",
        "    if parsed.tzinfo is None or parsed.utcoffset() is None:",
        "        raise ValueError(_PORTABLE_DATETIME_ERROR)",
        "    return parsed",
    ]


def _zod_datetime_helper() -> list[str]:
    return [
        (
            "const PORTABLE_DATETIME_PATTERN = "
            "/^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
            "(?:\\.[0-9]+)?(?:Z|[+-]([0-9]{2}):([0-9]{2}))$/;"
        ),
        "",
        "const portableDatetime = (): z.ZodType<string> =>",
        "  z.string().refine((value) => {",
        "    const match = PORTABLE_DATETIME_PATTERN.exec(value);",
        "    if (match === null) return false;",
        "    const year = Number(match[1]);",
        "    const month = Number(match[2]);",
        "    const day = Number(match[3]);",
        "    const hour = Number(match[4]);",
        "    const minute = Number(match[5]);",
        "    const second = Number(match[6]);",
        "    const offsetHour = Number(match[7] ?? 0);",
        "    const offsetMinute = Number(match[8] ?? 0);",
        "    const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);",
        "    const daysInMonth = month === 2 ? (leapYear ? 29 : 28) :",
        "      ([4, 6, 9, 11].includes(month) ? 30 : 31);",
        "    return year > 0 && month >= 1 && month <= 12 && day >= 1 && day <= daysInMonth",
        "      && hour <= 23 && minute <= 59 && second <= 59",
        "      && offsetHour <= 23 && offsetMinute <= 59;",
        '  }, { message: "Expected RFC 3339 datetime with a required Z or ±HH:MM offset" });',
    ]


def _header(comment: str, digest: str) -> list[str]:
    return [
        f"{comment} Generated by Contract4Agents codegen v{GENERATOR_VERSION}.",
        f"{comment} Contract digest: {digest}",
        f"{comment} DO NOT EDIT. Regenerate from the canonical contract IR.",
        "",
    ]


def _pydantic_model(type_def: TypeIR) -> list[str]:
    lines = [f"class {type_def.name}(BaseModel):", '    model_config = ConfigDict(extra="forbid")']
    if type_def.description:
        lines.insert(1, f'    """{_python_docstring(type_def.description)}"""')
    for type_field in type_def.fields:
        declaration = f"    {type_field.name}: {_python_type(type_field.type_ref)}"
        if type_field.has_default:
            declaration += f" = {_python_literal(type_field.default)}"
        elif isinstance(type_field.type_ref, NullableTypeRef):
            declaration += " = None"
        lines.append(declaration)
    return lines


def _python_type(type_ref: TypeRef) -> str:
    if isinstance(type_ref, PrimitiveTypeRef):
        return {
            "string": "str",
            "integer": "int",
            "float": "float",
            "boolean": "bool",
            "datetime": "Annotated[datetime, BeforeValidator(parse_portable_datetime)]",
        }[type_ref.name]
    if isinstance(type_ref, ConstrainedTypeRef):
        values = (
            ("ge", type_ref.minimum),
            ("le", type_ref.maximum),
            ("min_length", type_ref.min_length),
            ("max_length", type_ref.max_length),
        )
        constraints = ", ".join(f"{name}={value!r}" for name, value in values if value is not None)
        return f"Annotated[{_python_type(type_ref.item)}, Field({constraints})]"
    if isinstance(type_ref, NamedTypeRef):
        return type_ref.type_id.parts[0]
    if isinstance(type_ref, NullableTypeRef):
        return f"{_python_type(type_ref.item)} | None"
    if isinstance(type_ref, ListTypeRef):
        source = f"list[{_python_type(type_ref.item)}]"
        list_values = (("min_length", type_ref.min_items), ("max_length", type_ref.max_items))
        constraints = ", ".join(f"{name}={value!r}" for name, value in list_values if value is not None)
        return f"Annotated[{source}, Field({constraints})]" if constraints else source
    return f"dict[str, {_python_type(type_ref.value)}]"


def _typescript_type(type_ref: TypeRef) -> str:
    if isinstance(type_ref, PrimitiveTypeRef):
        return {
            "string": "string",
            "integer": "number",
            "float": "number",
            "boolean": "boolean",
            "datetime": "string",
        }[type_ref.name]
    if isinstance(type_ref, ConstrainedTypeRef):
        return _typescript_type(type_ref.item)
    if isinstance(type_ref, NamedTypeRef):
        return type_ref.type_id.parts[0]
    if isinstance(type_ref, NullableTypeRef):
        return f"{_typescript_type(type_ref.item)} | null"
    if isinstance(type_ref, ListTypeRef):
        return f"Array<{_typescript_type(type_ref.item)}>"
    return f"Record<string, {_typescript_type(type_ref.value)}>"


def _zod_type(type_ref: TypeRef) -> str:
    if isinstance(type_ref, PrimitiveTypeRef):
        return {
            "string": "z.string()",
            "integer": "z.number().int()",
            "float": "z.number()",
            "boolean": "z.boolean()",
            "datetime": "portableDatetime()",
        }[type_ref.name]
    if isinstance(type_ref, ConstrainedTypeRef):
        source = _zod_type(type_ref.item)
        if type_ref.item.name == "string":
            if type_ref.min_length is not None:
                source += (
                    f'.refine((value) => Array.from(value).length >= {type_ref.min_length}, '
                    f'{{ message: "String must contain at least {type_ref.min_length} Unicode code points" }})'
                )
            if type_ref.max_length is not None:
                source += (
                    f'.refine((value) => Array.from(value).length <= {type_ref.max_length}, '
                    f'{{ message: "String must contain at most {type_ref.max_length} Unicode code points" }})'
                )
            return source
        values = (("min", type_ref.minimum), ("max", type_ref.maximum))
        return source + "".join(f".{name}({value!r})" for name, value in values if value is not None)
    if isinstance(type_ref, NamedTypeRef):
        return f"z.lazy(() => {type_ref.type_id.parts[0]}Schema)"
    if isinstance(type_ref, NullableTypeRef):
        return f"{_zod_type(type_ref.item)}.nullable()"
    if isinstance(type_ref, ListTypeRef):
        source = f"z.array({_zod_type(type_ref.item)})"
        if type_ref.min_items is not None:
            source += f".min({type_ref.min_items})"
        if type_ref.max_items is not None:
            source += f".max({type_ref.max_items})"
        return source
    return f"z.record(z.string(), {_zod_type(type_ref.value)})"


def _python_literal(value: FrozenJsonValue) -> str:
    if value is None:
        return "None"
    if value is True:
        return "True"
    if value is False:
        return "False"
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, tuple):
        return "[" + ", ".join(_python_literal(item) for item in value) + "]"
    return "{" + ", ".join(
        f"{key!r}: {_python_literal(item)}" for key, item in sorted(value.items())
    ) + "}"


def _typescript_literal(value: FrozenJsonValue) -> str:
    return json.dumps(_plain_json(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _plain_json(value: FrozenJsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def _ordered_types(ir: CanonicalIR) -> tuple[TypeDeclarationIR, ...]:
    by_id = dict(ir.types.items())
    for type_def in by_id.values():
        _validate_identifier(type_def.name, f"type `{type_def.name}`")
        if isinstance(type_def, EnumIR):
            continue
        for type_field in type_def.fields:
            _validate_identifier(type_field.name, f"field `{type_def.name}.{type_field.name}`")
            for dependency in _named_dependencies(type_field.type_ref):
                if dependency not in by_id:
                    raise CodeGenerationError(
                        "CGEN001",
                        f"Type `{type_def.name}` references missing contract type `{dependency.parts[0]}`",
                    )

    ordered: list[TypeDeclarationIR] = []
    state: dict[SemanticId, int] = {}

    def visit(type_id: SemanticId) -> None:
        if state.get(type_id) == 2:
            return
        if state.get(type_id) == 1:
            return
        state[type_id] = 1
        type_def = by_id[type_id]
        dependencies = (
            {
                dependency
                for type_field in type_def.fields
                for dependency in _named_dependencies(type_field.type_ref)
                if dependency != type_id
            }
            if isinstance(type_def, TypeIR)
            else set()
        )
        for dependency in sorted(dependencies, key=str):
            visit(dependency)
        state[type_id] = 2
        ordered.append(type_def)

    for type_id in sorted(by_id, key=str):
        visit(type_id)
    return tuple(ordered)


def _named_dependencies(type_ref: TypeRef) -> Iterable[SemanticId]:
    if isinstance(type_ref, NamedTypeRef):
        yield type_ref.type_id
    elif isinstance(type_ref, NullableTypeRef | ListTypeRef):
        yield from _named_dependencies(type_ref.item)
    elif isinstance(type_ref, MapTypeRef):
        yield from _named_dependencies(type_ref.value)
    elif isinstance(type_ref, ConstrainedTypeRef):
        return


def _uses_primitive(types: tuple[TypeDeclarationIR, ...], primitive: str) -> bool:
    return any(
        _contains_primitive(type_field.type_ref, primitive)
        for type_def in types
        if isinstance(type_def, TypeIR)
        for type_field in type_def.fields
    )


def _contains_primitive(type_ref: TypeRef, primitive: str) -> bool:
    if isinstance(type_ref, PrimitiveTypeRef):
        return type_ref.name == primitive
    if isinstance(type_ref, ConstrainedTypeRef):
        return type_ref.item.name == primitive
    if isinstance(type_ref, NullableTypeRef | ListTypeRef):
        return _contains_primitive(type_ref.item, primitive)
    if isinstance(type_ref, MapTypeRef):
        return _contains_primitive(type_ref.value, primitive)
    return False


def _uses_constraints(types: tuple[TypeDeclarationIR, ...]) -> bool:
    return any(
        _contains_constraints(type_field.type_ref)
        for type_def in types
        if isinstance(type_def, TypeIR)
        for type_field in type_def.fields
    )


def _contains_constraints(type_ref: TypeRef) -> bool:
    if isinstance(type_ref, ConstrainedTypeRef):
        return True
    if isinstance(type_ref, NullableTypeRef):
        return _contains_constraints(type_ref.item)
    if isinstance(type_ref, ListTypeRef):
        return (
            type_ref.min_items is not None
            or type_ref.max_items is not None
            or _contains_constraints(type_ref.item)
        )
    if isinstance(type_ref, MapTypeRef):
        return _contains_constraints(type_ref.value)
    return False


def _validate_identifier(name: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(name) is None or keyword.iskeyword(name):
        raise CodeGenerationError("CGEN001", f"{label} is not a portable generated-code identifier")


def _python_docstring(value: str) -> str:
    return value.replace('"""', r'\"\"\"').replace("\n", " ")


__all__ = [
    "generate_code",
    "generate_pydantic_models",
    "generate_typescript_types",
    "generate_zod_schemas",
]
