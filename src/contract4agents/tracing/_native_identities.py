"""Private identity joins for native SDK hook and plugin objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeVar

from contract4agents.ir import CanonicalIR, SemanticId
from contract4agents.tracing._models import TraceSemanticRefs

_KeyT = TypeVar("_KeyT")


@dataclass(frozen=True)
class NativeTraceIdentityMap:
    """Stable semantic IDs keyed by native object identity and SDK-safe names."""

    agents_by_object: Mapping[int, SemanticId]
    agents_by_name: Mapping[str, SemanticId]
    grants_by_object: Mapping[int, tuple[SemanticId, ...]]
    grants_by_name: Mapping[str, tuple[SemanticId, ...]]
    composition_by_object: Mapping[int, SemanticId]
    composition_by_name: Mapping[str, SemanticId]

    @classmethod
    def from_graph(cls, graph: object) -> NativeTraceIdentityMap:
        agents = _identity_pairs(_semantic_objects(graph, "agents"), "agent")
        grants = _multi_identity_pairs(_semantic_objects(graph, "grant_objects"))
        composition = _identity_pairs(
            _semantic_objects(graph, "composition_objects"),
            "composition",
        )
        return cls(
            *agents,
            *grants,
            *composition,
        )

    def merge(self, other: NativeTraceIdentityMap) -> NativeTraceIdentityMap:
        return NativeTraceIdentityMap(
            _merge_map(self.agents_by_object, other.agents_by_object, "agent object"),
            _merge_map(self.agents_by_name, other.agents_by_name, "agent name"),
            _merge_multi_map(self.grants_by_object, other.grants_by_object),
            _merge_multi_map(self.grants_by_name, other.grants_by_name),
            _merge_map(
                self.composition_by_object,
                other.composition_by_object,
                "composition object",
            ),
            _merge_map(
                self.composition_by_name,
                other.composition_by_name,
                "composition name",
            ),
        )

    def agent_id(self, native: object | None) -> SemanticId | None:
        return _resolve(native, self.agents_by_object, self.agents_by_name)

    def tool_semantic(
        self,
        native: object | None,
        ir: CanonicalIR,
        *,
        fallback_agent: SemanticId | None,
    ) -> tuple[str, TraceSemanticRefs]:
        grant_ids = _resolve_many(
            native,
            self.grants_by_object,
            self.grants_by_name,
        )
        matching_grants = tuple(
            identifier
            for identifier in grant_ids
            if fallback_agent is None
            or (
                ir.grants.get(identifier) is not None
                and ir.grants[identifier].agent_id == fallback_agent
            )
        )
        grant_id = (
            matching_grants[0]
            if len(matching_grants) == 1
            else grant_ids[0]
            if len(grant_ids) == 1
            else None
        )
        if grant_id is not None:
            grant = ir.grants.get(grant_id)
            if grant is None:
                return "tool", TraceSemanticRefs(agent_id=fallback_agent)
            controls = tuple(
                control.id
                for control in ir.controls.values()
                if control.derived_from == grant.id
            )
            return "tool", TraceSemanticRefs(
                agent_id=grant.agent_id,
                capability_id=grant.capability_id,
                grant_id=grant.id,
                control_ids=controls,
                isolation_id=grant.isolation_id,
            )
        edge_id = _resolve(
            native,
            self.composition_by_object,
            self.composition_by_name,
        )
        if edge_id is not None:
            edge = ir.composition.get(edge_id)
            if edge is None:
                return "composition", TraceSemanticRefs(agent_id=fallback_agent)
            return "composition", TraceSemanticRefs(
                agent_id=edge.source_agent_id,
                composition_id=edge.id,
                isolation_id=edge.isolation_id,
            )
        return "tool", TraceSemanticRefs(agent_id=fallback_agent)


EMPTY_NATIVE_TRACE_IDENTITIES = NativeTraceIdentityMap({}, {}, {}, {}, {}, {})


def _semantic_objects(
    graph: object,
    attribute: str,
) -> tuple[tuple[SemanticId, object], ...]:
    values = getattr(graph, attribute, None)
    if not isinstance(values, Mapping):
        raise TypeError(f"Native graph `{attribute}` must be a mapping")
    selected: list[tuple[SemanticId, object]] = []
    for identifier, native in values.items():
        if not isinstance(identifier, SemanticId):
            raise TypeError(f"Native graph `{attribute}` keys must be SemanticIds")
        selected.append((identifier, native))
    return tuple(selected)


def _identity_pairs(
    values: tuple[tuple[SemanticId, object], ...],
    label: str,
) -> tuple[dict[int, SemanticId], dict[str, SemanticId]]:
    by_object: dict[int, SemanticId] = {}
    by_name: dict[str, SemanticId] = {}
    for identifier, native in values:
        object_id = id(native)
        existing_object = by_object.get(object_id)
        if existing_object is not None and existing_object != identifier:
            raise ValueError(f"One native {label} object maps to multiple semantic IDs")
        by_object[object_id] = identifier
        for name in _native_names(native):
            existing_name = by_name.get(name)
            if existing_name is not None and existing_name != identifier:
                raise ValueError(
                    f"Native {label} name `{name}` maps to multiple semantic IDs"
                )
            by_name[name] = identifier
    return by_object, by_name


def _multi_identity_pairs(
    values: tuple[tuple[SemanticId, object], ...],
) -> tuple[dict[int, tuple[SemanticId, ...]], dict[str, tuple[SemanticId, ...]]]:
    by_object: dict[int, list[SemanticId]] = {}
    by_name: dict[str, list[SemanticId]] = {}
    for identifier, native in values:
        by_object.setdefault(id(native), []).append(identifier)
        for name in _native_names(native):
            by_name.setdefault(name, []).append(identifier)
    return (
        {
            key: tuple(sorted(set(identifiers)))
            for key, identifiers in by_object.items()
        },
        {
            key: tuple(sorted(set(identifiers)))
            for key, identifiers in by_name.items()
        },
    )


def _native_names(native: object) -> tuple[str, ...]:
    names: list[str] = []
    for attribute in ("native_name", "name", "tool_name"):
        value = getattr(native, attribute, None)
        if isinstance(value, str) and value.strip():
            names.append(value)
    tool_spec = getattr(native, "tool_spec", None)
    if isinstance(tool_spec, Mapping):
        value = tool_spec.get("name")
        if isinstance(value, str) and value.strip():
            names.append(value)
    return tuple(dict.fromkeys(names))


def _resolve(
    native: object | None,
    by_object: Mapping[int, SemanticId],
    by_name: Mapping[str, SemanticId],
) -> SemanticId | None:
    if native is None:
        return None
    direct = by_object.get(id(native))
    if direct is not None:
        return direct
    if isinstance(native, str):
        return by_name.get(native)
    for name in _native_names(native):
        resolved = by_name.get(name)
        if resolved is not None:
            return resolved
    return None


def _resolve_many(
    native: object | None,
    by_object: Mapping[int, tuple[SemanticId, ...]],
    by_name: Mapping[str, tuple[SemanticId, ...]],
) -> tuple[SemanticId, ...]:
    if native is None:
        return ()
    direct = by_object.get(id(native))
    if direct is not None:
        return direct
    if isinstance(native, str):
        return by_name.get(native, ())
    identifiers: set[SemanticId] = set()
    for name in _native_names(native):
        identifiers.update(by_name.get(name, ()))
    return tuple(sorted(identifiers))


def _merge_map(
    first: Mapping[_KeyT, SemanticId],
    second: Mapping[_KeyT, SemanticId],
    label: str,
) -> dict[_KeyT, SemanticId]:
    result = dict(first)
    for key, identifier in second.items():
        existing = result.get(key)
        if existing is not None and existing != identifier:
            raise ValueError(f"Native {label} `{key}` maps to multiple semantic IDs")
        result[key] = identifier
    return result


def _merge_multi_map(
    first: Mapping[_KeyT, tuple[SemanticId, ...]],
    second: Mapping[_KeyT, tuple[SemanticId, ...]],
) -> dict[_KeyT, tuple[SemanticId, ...]]:
    result = dict(first)
    for key, identifiers in second.items():
        result[key] = tuple(sorted(set((*result.get(key, ()), *identifiers))))
    return result


__all__ = ["EMPTY_NATIVE_TRACE_IDENTITIES", "NativeTraceIdentityMap"]
