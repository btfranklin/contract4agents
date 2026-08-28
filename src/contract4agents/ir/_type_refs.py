"""Portable canonical type references and their small recursive parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from contract4agents.ir._ids import SemanticId, semantic_id

PrimitiveName = Literal["string", "integer", "float", "boolean", "datetime"]
PRIMITIVE_NAMES: frozenset[str] = frozenset({"string", "integer", "float", "boolean", "datetime"})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class PrimitiveTypeRef:
    name: PrimitiveName

    def __post_init__(self) -> None:
        if self.name not in PRIMITIVE_NAMES:
            raise ValueError(f"Unknown primitive type `{self.name}`")


@dataclass(frozen=True)
class ConstrainedTypeRef:
    item: PrimitiveTypeRef
    minimum: int | float | None = None
    maximum: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None

    def __post_init__(self) -> None:
        if all(
            value is None
            for value in (self.minimum, self.maximum, self.min_length, self.max_length)
        ):
            raise ValueError("A constrained type must declare at least one constraint")
        if self.item.name == "string":
            if self.minimum is not None or self.maximum is not None:
                raise ValueError("String constraints must use `min_length` or `max_length`")
        elif self.item.name in {"integer", "float"}:
            if self.min_length is not None or self.max_length is not None:
                raise ValueError("Numeric constraints must use `minimum` or `maximum`")
        else:
            raise ValueError(f"Primitive type `{self.item.name}` does not support constraints")
        if self.item.name == "integer" and any(
            value is not None and not isinstance(value, int)
            for value in (self.minimum, self.maximum)
        ):
            raise ValueError("Integer bounds must be integers")
        if any(
            value is not None and (not isinstance(value, int) or value < 0)
            for value in (self.min_length, self.max_length)
        ):
            raise ValueError("String length bounds must be non-negative integers")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("`minimum` cannot be greater than `maximum`")
        if self.min_length is not None and self.max_length is not None and self.min_length > self.max_length:
            raise ValueError("`min_length` cannot be greater than `max_length`")


@dataclass(frozen=True)
class NamedTypeRef:
    type_id: SemanticId

    def __post_init__(self) -> None:
        self.type_id.require_kind("type")


@dataclass(frozen=True)
class NullableTypeRef:
    item: TypeRef

    def __post_init__(self) -> None:
        if isinstance(self.item, NullableTypeRef):
            raise ValueError("A nullable type cannot wrap another nullable type")


@dataclass(frozen=True)
class ListTypeRef:
    item: TypeRef
    min_items: int | None = None
    max_items: int | None = None

    def __post_init__(self) -> None:
        if any(
            value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0)
            for value in (self.min_items, self.max_items)
        ):
            raise ValueError("List item bounds must be non-negative integers")
        if self.min_items is not None and self.max_items is not None and self.min_items > self.max_items:
            raise ValueError("`min_items` cannot be greater than `max_items`")


@dataclass(frozen=True)
class MapTypeRef:
    value: TypeRef


TypeRef: TypeAlias = (
    PrimitiveTypeRef | ConstrainedTypeRef | NamedTypeRef | NullableTypeRef | ListTypeRef | MapTypeRef
)


def parse_type_ref(source: str) -> TypeRef:
    """Parse the complete portable type-reference subset."""

    parser = _TypeRefParser(source)
    result = parser.parse()
    parser.skip_space()
    if not parser.at_end:
        raise ValueError(f"Unexpected type-reference text at column {parser.position + 1}: {source!r}")
    return result


def format_type_ref(type_ref: TypeRef) -> str:
    """Render a type reference to its unique canonical spelling."""

    if isinstance(type_ref, PrimitiveTypeRef):
        return type_ref.name
    if isinstance(type_ref, ConstrainedTypeRef):
        values = (
            ("minimum", type_ref.minimum),
            ("maximum", type_ref.maximum),
            ("min_length", type_ref.min_length),
            ("max_length", type_ref.max_length),
        )
        constraints = ",".join(
            f"{name}={_format_number(value)}" for name, value in values if value is not None
        )
        return f"{type_ref.item.name}({constraints})"
    if isinstance(type_ref, NamedTypeRef):
        return str(type_ref.type_id)
    if isinstance(type_ref, NullableTypeRef):
        return f"{format_type_ref(type_ref.item)}?"
    if isinstance(type_ref, ListTypeRef):
        constraints = ",".join(
            f"{name}={_format_number(value)}"
            for name, value in (("min_items", type_ref.min_items), ("max_items", type_ref.max_items))
            if value is not None
        )
        suffix = f"({constraints})" if constraints else ""
        return f"list[{format_type_ref(type_ref.item)}]{suffix}"
    return f"map[string,{format_type_ref(type_ref.value)}]"


class _TypeRefParser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.position = 0

    @property
    def at_end(self) -> bool:
        return self.position >= len(self.source)

    def skip_space(self) -> None:
        while not self.at_end and self.source[self.position].isspace():
            self.position += 1

    def parse(self) -> TypeRef:
        self.skip_space()
        token = self._identifier()
        self.skip_space()
        if token == "list" and self._peek("["):
            self.position += 1
            item = self.parse()
            self._expect("]")
            result: TypeRef = ListTypeRef(item)
        elif token == "map" and self._peek("["):
            self.position += 1
            key = self._identifier()
            if key != "string":
                raise ValueError("Portable map keys must be `string`")
            self._expect(",")
            result = MapTypeRef(self.parse())
            self._expect("]")
        elif token == "type" and self._peek(":"):
            self.position += 1
            result = NamedTypeRef(semantic_id("type", self._identifier()))
        elif token in PRIMITIVE_NAMES:
            result = PrimitiveTypeRef(cast(PrimitiveName, token))
            if self._peek("("):
                result = self._constraints(result)
        else:
            result = NamedTypeRef(semantic_id("type", token))
        if self._peek("("):
            if isinstance(result, ListTypeRef):
                result = self._list_constraints(result)
            elif isinstance(result, MapTypeRef):
                raise ValueError("Map types do not support type constraints")
            else:
                raise ValueError("Named types do not support type constraints")
        self.skip_space()
        if self._peek("?"):
            self.position += 1
            result = NullableTypeRef(result)
        return result

    def _constraints(self, item: PrimitiveTypeRef) -> ConstrainedTypeRef:
        values = self._constraint_values({"minimum", "maximum", "min_length", "max_length"})
        return ConstrainedTypeRef(
            item,
            minimum=values.get("minimum"),
            maximum=values.get("maximum"),
            min_length=_integer_constraint(values, "min_length"),
            max_length=_integer_constraint(values, "max_length"),
        )

    def _list_constraints(self, item: ListTypeRef) -> ListTypeRef:
        self.skip_space()
        if self.source.startswith("()", self.position):
            self.position += 2
            raise ValueError("A constrained list must declare at least one bound")
        values = self._constraint_values({"min_items", "max_items"})
        return ListTypeRef(
            item=item.item,
            min_items=_integer_constraint(values, "min_items"),
            max_items=_integer_constraint(values, "max_items"),
        )

    def _constraint_values(self, allowed: set[str]) -> dict[str, int | float]:
        self._expect("(")
        values: dict[str, int | float] = {}
        while True:
            name = self._identifier()
            if name not in allowed:
                accepted = ", ".join(sorted(allowed))
                raise ValueError(f"Unknown type constraint `{name}`; expected one of: {accepted}")
            if name in values:
                raise ValueError(f"Duplicate type constraint `{name}`")
            self._expect("=")
            values[name] = self._number()
            self.skip_space()
            if self._peek(")"):
                self.position += 1
                break
            self._expect(",")
        return values

    def _number(self) -> int | float:
        self.skip_space()
        match = re.match(r"[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", self.source[self.position :])
        if match is None:
            raise ValueError(f"Expected a numeric constraint at column {self.position + 1}: {self.source!r}")
        raw = match.group(0)
        self.position += len(raw)
        return float(raw) if "." in raw or "e" in raw.lower() else int(raw)

    def _identifier(self) -> str:
        self.skip_space()
        match = _IDENTIFIER.match(self.source, self.position)
        if match is None:
            raise ValueError(f"Expected a type name at column {self.position + 1}: {self.source!r}")
        self.position = match.end()
        return match.group(0)

    def _peek(self, token: str) -> bool:
        self.skip_space()
        return self.source.startswith(token, self.position)

    def _expect(self, token: str) -> None:
        self.skip_space()
        if not self.source.startswith(token, self.position):
            raise ValueError(f"Expected `{token}` at column {self.position + 1}: {self.source!r}")
        self.position += len(token)


def _integer_constraint(values: dict[str, int | float], name: str) -> int | None:
    value = values.get(name)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"`{name}` must be an integer")
    return value


def _format_number(value: int | float) -> str:
    return repr(value)


__all__ = [
    "ConstrainedTypeRef",
    "ListTypeRef",
    "MapTypeRef",
    "NamedTypeRef",
    "NullableTypeRef",
    "PRIMITIVE_NAMES",
    "PrimitiveName",
    "PrimitiveTypeRef",
    "TypeRef",
    "format_type_ref",
    "parse_type_ref",
]
