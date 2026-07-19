"""Assurance-relevant semantic diffs for canonical contracts and plans."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from contract4agents.ir import CanonicalIR, EnumIR, SemanticId, TypeIR, format_type_ref
from contract4agents.planning import MaterializationPlan

DiffArea = Literal[
    "capability_access",
    "authorization",
    "approval",
    "isolation",
    "schema",
    "context_exposure",
    "enforcement",
    "audience",
    "quality",
    "eval_coverage",
    "model",
]
DiffImpact = Literal["informational", "review", "breaking", "security_critical"]
DiffChange = Literal["added", "removed", "changed"]

_OUTCOME_RANK = {"exact": 0, "host_enforced": 1, "emulated": 2, "degraded": 3, "unsupported": 4}


class _HasOutcome(Protocol):
    @property
    def outcome(self) -> str: ...


@dataclass(frozen=True)
class SemanticDiffEntry:
    area: DiffArea
    change: DiffChange
    impact: DiffImpact
    semantic_id: str
    summary: str
    before: object | None = None
    after: object | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "after": self.after,
            "area": self.area,
            "before": self.before,
            "change": self.change,
            "impact": self.impact,
            "semantic_id": self.semantic_id,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class SemanticDiff:
    contract_changes: tuple[SemanticDiffEntry, ...] = ()
    plan_changes: tuple[SemanticDiffEntry, ...] = ()

    @property
    def changes(self) -> tuple[SemanticDiffEntry, ...]:
        return self.contract_changes + self.plan_changes

    @property
    def has_breaking_changes(self) -> bool:
        return any(item.impact in {"breaking", "security_critical"} for item in self.changes)

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_changes": [item.to_dict() for item in self.contract_changes],
            "has_breaking_changes": self.has_breaking_changes,
            "plan_changes": [item.to_dict() for item in self.plan_changes],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def diff_contracts(before: CanonicalIR, after: CanonicalIR) -> tuple[SemanticDiffEntry, ...]:
    """Classify contract changes by their operational and assurance significance."""

    entries: list[SemanticDiffEntry] = []
    entries.extend(_diff_grants(before, after))
    entries.extend(_diff_types(before, after))
    entries.extend(_diff_contexts(before, after))
    entries.extend(_diff_isolation(before, after))
    entries.extend(_diff_controls(before, after))
    entries.extend(_diff_named_coverage(before.qualities, after.qualities, "quality"))
    entries.extend(_diff_named_coverage(before.evals, after.evals, "eval_coverage"))
    return _sorted(entries)


def diff_materialization_plans(
    before: MaterializationPlan,
    after: MaterializationPlan,
) -> tuple[SemanticDiffEntry, ...]:
    """Classify target resolution changes without comparing native SDK objects."""

    entries: list[SemanticDiffEntry] = []
    for identifier in sorted(set(before.agents) | set(after.agents), key=str):
        old_agent = before.agents.get(identifier)
        new_agent = after.agents.get(identifier)
        if old_agent is not None and new_agent is not None and old_agent.model != new_agent.model:
            entries.append(
                _entry(
                    "model",
                    "changed",
                    "review",
                    identifier,
                    f"Resolved model changed from `{old_agent.model}` to `{new_agent.model}`.",
                    old_agent.model,
                    new_agent.model,
                )
            )
    entries.extend(_diff_plan_outcomes(before.grants, after.grants))
    entries.extend(_diff_plan_outcomes(before.controls, after.controls))
    entries.extend(_diff_plan_outcomes(before.composition, after.composition))
    for identifier in sorted(set(before.isolation) | set(after.isolation), key=str):
        old_iso = before.isolation.get(identifier)
        new_iso = after.isolation.get(identifier)
        if old_iso is None or new_iso is None:
            continue
        for dimension in sorted(set(old_iso.dimensions) | set(new_iso.dimensions)):
            old_dimension = old_iso.dimensions.get(dimension)
            new_dimension = new_iso.dimensions.get(dimension)
            if old_dimension is None or new_dimension is None:
                continue
            if old_dimension.outcome != new_dimension.outcome:
                worsened = _OUTCOME_RANK[new_dimension.outcome] > _OUTCOME_RANK[old_dimension.outcome]
                entries.append(
                    _entry(
                        "isolation",
                        "changed",
                        "security_critical" if worsened else "review",
                        f"{identifier}:{dimension}",
                        f"Isolation mapping changed from `{old_dimension.outcome}` to `{new_dimension.outcome}`.",
                        old_dimension.outcome,
                        new_dimension.outcome,
                    )
                )
    return _sorted(entries)


def _diff_plan_outcomes(
    old_values: Mapping[SemanticId, _HasOutcome],
    new_values: Mapping[SemanticId, _HasOutcome],
) -> list[SemanticDiffEntry]:
    entries: list[SemanticDiffEntry] = []
    for identifier in sorted(set(old_values) | set(new_values), key=str):
        old = old_values.get(identifier)
        new = new_values.get(identifier)
        if old is None or new is None or old.outcome == new.outcome:
            continue
        old_outcome = old.outcome
        new_outcome = new.outcome
        worsened = _OUTCOME_RANK[new_outcome] > _OUTCOME_RANK[old_outcome]
        entries.append(
            _entry(
                "enforcement",
                "changed",
                "security_critical" if worsened else "review",
                identifier,
                f"Mapping outcome changed from `{old_outcome}` to `{new_outcome}`.",
                old_outcome,
                new_outcome,
            )
        )
    return entries


def semantic_diff(
    before_contract: CanonicalIR,
    after_contract: CanonicalIR,
    before_plan: MaterializationPlan | None = None,
    after_plan: MaterializationPlan | None = None,
) -> SemanticDiff:
    plans: tuple[SemanticDiffEntry, ...] = ()
    if before_plan is not None and after_plan is not None:
        plans = diff_materialization_plans(before_plan, after_plan)
    return SemanticDiff(diff_contracts(before_contract, after_contract), plans)


def _diff_grants(before: CanonicalIR, after: CanonicalIR) -> list[SemanticDiffEntry]:
    entries: list[SemanticDiffEntry] = []
    for identifier in sorted(set(before.grants) | set(after.grants), key=str):
        old = before.grants.get(identifier)
        new = after.grants.get(identifier)
        if old is None and new is not None:
            impact: DiffImpact = "security_critical" if new.availability == "enabled" else "informational"
            entries.append(
                _entry(
                    "capability_access",
                    "added",
                    impact,
                    identifier,
                    f"Capability grant added with availability `{new.availability}`.",
                    None,
                    new.availability,
                )
            )
            continue
        if old is not None and new is None:
            entries.append(
                _entry(
                    "capability_access",
                    "removed",
                    "review",
                    identifier,
                    "Capability grant removed.",
                    old.availability,
                    None,
                )
            )
            continue
        assert old is not None and new is not None
        if old.availability != new.availability:
            enabled = new.availability == "enabled"
            entries.append(
                _entry(
                    "capability_access",
                    "changed",
                    "security_critical" if enabled else "review",
                    identifier,
                    f"Availability changed from `{old.availability}` to `{new.availability}`.",
                    old.availability,
                    new.availability,
                )
            )
        if old.authorization != new.authorization:
            weakened = old.authorization == "approval_required" and new.authorization == "preapproved"
            entries.append(
                _entry(
                    "authorization",
                    "changed",
                    "security_critical" if weakened else "review",
                    identifier,
                    f"Authorization changed from `{old.authorization}` to `{new.authorization}`.",
                    old.authorization,
                    new.authorization,
                )
            )
    return entries


def _diff_types(before: CanonicalIR, after: CanonicalIR) -> list[SemanticDiffEntry]:
    entries: list[SemanticDiffEntry] = []
    for identifier in sorted(set(before.types) | set(after.types), key=str):
        old = before.types.get(identifier)
        new = after.types.get(identifier)
        if old is None and new is not None:
            entries.append(_entry("schema", "added", "informational", identifier, "Type added."))
            continue
        if old is not None and new is None:
            entries.append(_entry("schema", "removed", "breaking", identifier, "Type removed."))
            continue
        assert old is not None and new is not None
        if type(old) is not type(new):
            entries.append(_entry("schema", "changed", "breaking", identifier, "Type kind changed."))
            continue
        if isinstance(old, EnumIR) and isinstance(new, EnumIR):
            old_values = set(old.values)
            new_values = set(new.values)
            for value in sorted(old_values - new_values):
                entries.append(
                    _entry(
                        "schema",
                        "removed",
                        "breaking",
                        f"{identifier}:{value}",
                        f"Enum value `{value}` removed.",
                    )
                )
            for value in sorted(new_values - old_values):
                entries.append(
                    _entry(
                        "schema",
                        "added",
                        "review",
                        f"{identifier}:{value}",
                        f"Enum value `{value}` added.",
                    )
                )
            continue
        assert isinstance(old, TypeIR) and isinstance(new, TypeIR)
        old_fields = {item.name: item for item in old.fields}
        new_fields = {item.name: item for item in new.fields}
        for name in sorted(set(old_fields) | set(new_fields)):
            old_field = old_fields.get(name)
            new_field = new_fields.get(name)
            field_id = f"{identifier}:{name}"
            if old_field is None and new_field is not None:
                breaking = not new_field.has_default and not format_type_ref(new_field.type_ref).endswith("?")
                entries.append(
                    _entry(
                        "schema",
                        "added",
                        "breaking" if breaking else "review",
                        field_id,
                        "Required schema field added." if breaking else "Optional/defaulted schema field added.",
                    )
                )
            elif old_field is not None and new_field is None:
                entries.append(_entry("schema", "removed", "breaking", field_id, "Schema field removed."))
            elif old_field is not None and new_field is not None:
                old_type = format_type_ref(old_field.type_ref)
                new_type = format_type_ref(new_field.type_ref)
                if old_type != new_type:
                    entries.append(
                        _entry(
                            "schema",
                            "changed",
                            "breaking",
                            field_id,
                            f"Field type changed from `{old_type}` to `{new_type}`.",
                            old_type,
                            new_type,
                        )
                    )
    return entries


def _diff_contexts(before: CanonicalIR, after: CanonicalIR) -> list[SemanticDiffEntry]:
    entries: list[SemanticDiffEntry] = []
    for identifier in sorted(set(before.contexts) | set(after.contexts), key=str):
        old = before.contexts.get(identifier)
        new = after.contexts.get(identifier)
        if old is None and new is not None:
            entries.append(
                _entry(
                    "context_exposure",
                    "added",
                    "security_critical",
                    identifier,
                    f"Context exposure added from `{new.origin}`.",
                )
            )
        elif old is not None and new is None:
            entries.append(_entry("context_exposure", "removed", "review", identifier, "Context exposure removed."))
        elif old is not None and new is not None and (old.origin, old.origin_id) != (new.origin, new.origin_id):
            entries.append(
                _entry(
                    "context_exposure",
                    "changed",
                    "security_critical",
                    identifier,
                    "Context provenance changed.",
                    f"{old.origin}:{old.origin_id}",
                    f"{new.origin}:{new.origin_id}",
                )
            )
    return entries


def _diff_isolation(before: CanonicalIR, after: CanonicalIR) -> list[SemanticDiffEntry]:
    entries: list[SemanticDiffEntry] = []
    dimensions = ("context", "capabilities", "state", "filesystem", "network", "secrets", "return_channel")
    for identifier in sorted(set(before.isolation_profiles) | set(after.isolation_profiles), key=str):
        old = before.isolation_profiles.get(identifier)
        new = after.isolation_profiles.get(identifier)
        if old is None and new is not None:
            entries.append(_entry("isolation", "added", "review", identifier, "Isolation profile added."))
            continue
        if old is not None and new is None:
            entries.append(
                _entry(
                    "isolation",
                    "removed",
                    "security_critical",
                    identifier,
                    "Isolation profile removed.",
                )
            )
            continue
        assert old is not None and new is not None
        for dimension in dimensions:
            old_value = getattr(old, dimension)
            new_value = getattr(new, dimension)
            if old_value != new_value:
                entries.append(
                    _entry(
                        "isolation",
                        "changed",
                        "security_critical",
                        f"{identifier}:{dimension}",
                        f"Isolation requirement changed from `{old_value}` to `{new_value}`.",
                        old_value,
                        new_value,
                    )
                )
    return entries


def _diff_controls(before: CanonicalIR, after: CanonicalIR) -> list[SemanticDiffEntry]:
    entries: list[SemanticDiffEntry] = []
    for identifier in sorted(set(before.controls) | set(after.controls), key=str):
        old = before.controls.get(identifier)
        new = after.controls.get(identifier)
        if old is None and new is not None:
            entries.append(_entry("approval", "added", "review", identifier, "Control added."))
            continue
        if old is not None and new is None:
            entries.append(
                _entry(
                    "approval",
                    "removed",
                    "security_critical" if old.required else "review",
                    identifier,
                    "Required control removed." if old.required else "Advisory control removed.",
                )
            )
            continue
        assert old is not None and new is not None
        if old.required != new.required:
            entries.append(
                _entry(
                    "approval",
                    "changed",
                    "security_critical" if not new.required else "review",
                    identifier,
                    f"Control requiredness changed from `{old.required}` to `{new.required}`.",
                    old.required,
                    new.required,
                )
            )
        if old.audience != new.audience:
            entries.append(
                _entry(
                    "audience",
                    "changed",
                    "security_critical" if set(new.audience) - set(old.audience) else "review",
                    identifier,
                    "Control audience changed.",
                    list(old.audience),
                    list(new.audience),
                )
            )
    return entries


def _diff_named_coverage(
    old_values: Mapping[SemanticId, object],
    new_values: Mapping[SemanticId, object],
    area: DiffArea,
) -> list[SemanticDiffEntry]:
    entries: list[SemanticDiffEntry] = []
    for identifier in sorted(set(old_values) | set(new_values), key=str):
        old = old_values.get(identifier)
        new = new_values.get(identifier)
        if old is None and new is not None:
            entries.append(
                _entry(
                    area,
                    "added",
                    "informational",
                    identifier,
                    f"{area.replace('_', ' ').title()} added.",
                )
            )
        elif old is not None and new is None:
            entries.append(_entry(area, "removed", "review", identifier, f"{area.replace('_', ' ').title()} removed."))
    return entries


def _entry(
    area: DiffArea,
    change: DiffChange,
    impact: DiffImpact,
    identifier: SemanticId | str,
    summary: str,
    before: object | None = None,
    after: object | None = None,
) -> SemanticDiffEntry:
    return SemanticDiffEntry(area, change, impact, str(identifier), summary, before, after)


def _sorted(entries: list[SemanticDiffEntry]) -> tuple[SemanticDiffEntry, ...]:
    return tuple(sorted(entries, key=lambda item: (item.area, item.semantic_id, item.change, item.summary)))


__all__ = [
    "DiffArea",
    "DiffChange",
    "DiffImpact",
    "SemanticDiff",
    "SemanticDiffEntry",
    "diff_contracts",
    "diff_materialization_plans",
    "semantic_diff",
]
