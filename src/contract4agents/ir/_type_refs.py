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


@dataclass(frozen=True)
class MapTypeRef:
    value: TypeRef


TypeRef: TypeAlias = PrimitiveTypeRef | NamedTypeRef | NullableTypeRef | ListTypeRef | MapTypeRef


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
    if isinstance(type_ref, NamedTypeRef):
        return str(type_ref.type_id)
    if isinstance(type_ref, NullableTypeRef):
        return f"{format_type_ref(type_ref.item)}?"
    if isinstance(type_ref, ListTypeRef):
        return f"list[{format_type_ref(type_ref.item)}]"
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
        else:
            result = NamedTypeRef(semantic_id("type", token))
        self.skip_space()
        if self._peek("?"):
            self.position += 1
            result = NullableTypeRef(result)
        return result

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


__all__ = [
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
