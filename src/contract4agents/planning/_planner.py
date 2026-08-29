"""Generic target/profile resolution from canonical IR and target bindings."""

from __future__ import annotations

from collections.abc import Mapping

from contract4agents.expressions import ExpressionError, parse_operational_requirement
from contract4agents.ir import (
    CanonicalIR,
    CapabilityIR,
    FrozenMap,
    IsolationProfileIR,
    SemanticId,
    contract_digest,
)
from contract4agents.planning._errors import PlanningError, PlanningIssue
from contract4agents.planning._models import (
    AdapterPlan,
    AgentPlan,
    BindingExecution,
    BindingKind,
    BindingPlan,
    BindingResolution,
    CompositionMappingPlan,
    ControlMappingPlan,
    GrantMappingPlan,
    HostObligationPlan,
    IsolationDimensionPlan,
    IsolationMappingPlan,
    MappingOutcome,
    MappingSupport,
    MaterializationPlan,
    OperationalControlMappingPlan,
    PlannerCapabilities,
    frozen_json_mapping,
)
from contract4agents.target_bindings import BindingEntry, TargetBinding, TargetBindings, TargetProfile
from contract4agents.target_bindings._sensitive import target_sensitive_option_paths

_OUTCOME_RANK: dict[MappingOutcome, int] = {
    "exact": 0,
    "host_enforced": 1,
    "emulated": 2,
    "degraded": 3,
    "unsupported": 4,
}


def plan_materialization(
    ir: CanonicalIR,
    bindings: TargetBindings,
    *,
    target: str,
    profile: str,
    capabilities: PlannerCapabilities,
    artifact_digests: Mapping[str, str] | None = None,
) -> MaterializationPlan:
    """Resolve a target plan or fail before any materializer receives it."""

    issues: list[PlanningIssue] = []
    target_binding = bindings.targets.get(target)
    if target_binding is None:
        raise PlanningError((PlanningIssue("PLN001", f"Unknown target `{target}`"),))
    target_profile = target_binding.profiles.get(profile)
    if target_profile is None:
        raise PlanningError((PlanningIssue("PLN002", f"Unknown profile `{profile}` for target `{target}`"),))
    if capabilities.adapter != target_binding.adapter:
        raise PlanningError(
            (
                PlanningIssue(
                    "PLN003",
                    f"Planner capabilities for `{capabilities.adapter}` do not match "
                    f"adapter `{target_binding.adapter}`",
                ),
            )
        )

    sensitive_paths = target_sensitive_option_paths(target, target_binding)
    if sensitive_paths:
        raise PlanningError(
            tuple(
                PlanningIssue(
                    "PLN011",
                    f"Target option `{path}` cannot contain credential material. "
                    "Use the host environment or a credential provider.",
                )
                for path in sensitive_paths
            )
        )

    agents = _resolve_agents(ir, target_profile, issues)
    resolved_bindings, binding_support = _resolve_bindings(
        ir,
        target_binding,
        capabilities,
        issues,
    )
    obligations: dict[tuple[str, SemanticId | None], HostObligationPlan] = {}
    expected_event_types = set(capabilities.expected_event_types)
    for binding in resolved_bindings.values():
        _consume_support(binding_support[binding.id], binding.id, obligations, expected_event_types)
        if binding.execution == "host":
            _add_obligation(
                obligations,
                HostObligationPlan(
                    "host.provide_binding",
                    f"Provide the host implementation for `{binding.id}`.",
                    binding.id,
                ),
            )

    grants, approval_support = _resolve_grants(
        ir,
        resolved_bindings,
        target_binding,
        capabilities,
        issues,
        obligations,
        expected_event_types,
    )
    composition = _resolve_composition(ir, capabilities, issues, obligations, expected_event_types)
    controls = _resolve_controls(
        ir,
        agents,
        resolved_bindings,
        grants,
        composition,
        capabilities,
        approval_support,
        issues,
        obligations,
        expected_event_types,
    )
    isolation = _resolve_isolation(
        ir,
        target_binding,
        target_profile,
        capabilities,
        issues,
        obligations,
        expected_event_types,
    )
    operational_controls = _resolve_operational_controls(
        ir,
        issues,
        obligations,
        expected_event_types,
    )
    if issues:
        raise PlanningError(tuple(issues))
    resolved_artifact_digests = artifact_digests or {}

    return MaterializationPlan(
        contract_digest=contract_digest(ir),
        target=target,
        profile=profile,
        adapter=AdapterPlan(target_binding.adapter, capabilities.version),
        agents=FrozenMap((item.id, item) for item in agents.values()),
        bindings=FrozenMap((item.id, item) for item in resolved_bindings.values()),
        grants=FrozenMap((item.id, item) for item in grants.values()),
        composition=FrozenMap((item.id, item) for item in composition.values()),
        controls=FrozenMap((item.id, item) for item in controls.values()),
        isolation=FrozenMap((item.id, item) for item in isolation.values()),
        artifact_digests=FrozenMap(
            (name, resolved_artifact_digests[name])
            for name in sorted(resolved_artifact_digests)
        ),
        host_obligations=tuple(
            sorted(obligations.values(), key=lambda item: (item.code, str(item.semantic_id), item.description))
        ),
        expected_event_types=tuple(sorted(expected_event_types)),
        operational_controls=FrozenMap(
            (item.id, item) for item in operational_controls.values()
        ),
    )


def _resolve_operational_controls(
    ir: CanonicalIR,
    issues: list[PlanningIssue],
    obligations: dict[tuple[str, SemanticId | None], HostObligationPlan],
    event_types: set[str],
) -> dict[SemanticId, OperationalControlMappingPlan]:
    """Resolve the intentionally small single-run operational-control surface."""

    result: dict[SemanticId, OperationalControlMappingPlan] = {}
    for control_id, control in ir.operational_controls.items():
        outcome: MappingOutcome = "emulated"
        mechanism: str | None = "contract4agents.single_run_operational_assessor"
        evidence: tuple[str, ...]
        if control.window is not None:
            outcome = "unsupported"
            mechanism = None
            evidence = ()
        else:
            try:
                parsed = parse_operational_requirement(control.requirement)
            except ExpressionError:
                outcome = "unsupported"
                mechanism = None
                evidence = ()
            else:
                if parsed.metric == "duration":
                    evidence = ("agent.started", "agent.completed", "agent.failed")
                elif parsed.metric in {"provider_request_count", "input_tokens", "output_tokens", "total_tokens"}:
                    evidence = ("provider.usage.reported",)
                elif parsed.metric == "failed_provider_call_count":
                    evidence = ("provider.outcome.reported",)
                else:
                    evidence = ("attempt.selected",)
                event_types.update(evidence)
        if outcome == "unsupported":
            # The plan is fail-closed for declared operational requirements.
            pass
        else:
            _add_obligation(
                obligations,
                HostObligationPlan(
                    "host.provide_operational_evidence",
                    f"Provide normalized trace and closure evidence for operational control `{control_id}`.",
                    control_id,
                ),
            )
        result[control_id] = OperationalControlMappingPlan(
            id=control_id,
            agent_id=control.agent_id,
            severity=control.severity,
            requirement=control.requirement,
            window=control.window,
            outcome=outcome,
            mechanism=mechanism,
            expected_evidence=evidence,
        )
    return result


def _resolve_agents(
    ir: CanonicalIR,
    profile: TargetProfile,
    issues: list[PlanningIssue],
) -> dict[SemanticId, AgentPlan]:
    result: dict[SemanticId, AgentPlan] = {}
    known_names = {agent.name for agent in ir.agents.values()}
    for stale_name in sorted(set(profile.agents) - known_names):
        issues.append(PlanningIssue("PLN004", f"Profile configures unknown agent `{stale_name}`"))
    for agent_id, agent in ir.agents.items():
        agent_profile = profile.agents.get(agent.name)
        model = agent_profile.model if agent_profile and agent_profile.model else profile.default_model
        if model is None:
            issues.append(PlanningIssue("PLN005", f"No model resolved for agent `{agent.name}`", agent_id))
            continue
        options: dict[str, object] = dict(profile.options)
        if agent_profile is not None:
            options.update(agent_profile.options)
        try:
            frozen_options = frozen_json_mapping(options)
        except (TypeError, ValueError) as exc:
            issues.append(
                PlanningIssue(
                    "PLN010",
                    f"Model options for agent `{agent.name}` are not portable JSON: {exc}",
                    agent_id,
                )
            )
            continue
        result[agent_id] = AgentPlan(
            id=agent_id,
            name=agent.name,
            model=model,
            model_options=frozen_options,
            output_type=agent.output_type,
            parameters=agent.parameters,
        )
    return result


def _resolve_bindings(
    ir: CanonicalIR,
    target: TargetBinding,
    capabilities: PlannerCapabilities,
    issues: list[PlanningIssue],
) -> tuple[dict[SemanticId, BindingPlan], dict[SemanticId, MappingSupport]]:
    result: dict[SemanticId, BindingPlan] = {}
    support: dict[SemanticId, MappingSupport] = {}
    enabled = {grant.capability_id for grant in ir.grants.values() if grant.availability == "enabled"}
    for capability_id, capability in ir.capabilities.items():
        if capability.kind == "tool" and capability_id not in enabled:
            continue
        section = target.tools if capability.kind == "tool" else target.datasources
        entry = section.get(capability.name)
        if entry is None:
            issues.append(
                PlanningIssue(
                    "PLN006",
                    f"No {capability.kind} binding for `{capability.name}`",
                    capability_id,
                )
            )
            continue
        resolved = _binding_plan(
            ir,
            capability_id,
            capability,
            capability.kind,
            entry,
            capabilities,
            issues,
        )
        if resolved is not None:
            plan, mapping = resolved
            result[capability_id] = plan
            support[capability_id] = mapping
    for external_id, external in ir.external_contexts.items():
        entry = target.external_context.get(external.name)
        if entry is None:
            issues.append(PlanningIssue("PLN006", f"No external-context binding for `{external.name}`", external_id))
            continue
        resolved = _binding_plan(
            ir,
            external_id,
            None,
            "external",
            entry,
            capabilities,
            issues,
        )
        if resolved is not None:
            plan, mapping = resolved
            result[external_id] = plan
            support[external_id] = mapping
    return result, support


def _binding_plan(
    ir: CanonicalIR,
    identifier: SemanticId,
    capability: CapabilityIR | None,
    kind: BindingKind,
    entry: BindingEntry,
    capabilities: PlannerCapabilities,
    issues: list[PlanningIssue],
) -> tuple[BindingPlan, MappingSupport] | None:
    locator_families = _locator_families(entry.values)
    if not locator_families:
        issues.append(PlanningIssue("PLN007", f"Binding `{identifier}` has no implementation locator", identifier))
        return None
    if capabilities.mapping_resolver is None:
        resolution = _default_binding_resolution(entry.values)
    else:
        resolution = capabilities.mapping_resolver.resolve_binding(
            kind=kind,
            locator=entry.values,
        )
        contextual_resolver = getattr(
            capabilities.mapping_resolver,
            "binding_support",
            None,
        )
        if contextual_resolver is not None:
            contextual = contextual_resolver(
                ir=ir,
                capability=capability,
                kind=kind,
                locator=entry.values,
                declared=resolution,
            )
            if contextual is not None:
                resolution = contextual
    _require_mapping(
        resolution.support.outcome,
        identifier,
        f"{kind} binding",
        issues,
        detail=f"adapter `{capabilities.adapter}` does not support this locator",
    )
    if resolution.support.outcome in {"degraded", "unsupported"}:
        return None
    mechanism = resolution.support.mechanism
    if mechanism is None:
        raise AssertionError("Supported binding resolution must declare a mechanism")
    try:
        locator = frozen_json_mapping(entry.values)
    except (TypeError, ValueError) as exc:
        issues.append(PlanningIssue("PLN010", f"Binding `{identifier}` is not portable JSON: {exc}", identifier))
        return None
    return (
        BindingPlan(
            id=identifier,
            kind=kind,
            locator=locator,
            outcome=resolution.support.outcome,
            mechanism=mechanism,
            execution=resolution.execution,
        ),
        resolution.support,
    )


def _default_binding_resolution(locator: Mapping[str, object]) -> BindingResolution:
    families = _locator_families(locator)
    if families == {"host"} and "python" in locator and not ({"typescript", "module"} & set(locator)):
        return BindingResolution(
            "host",
            MappingSupport("exact", "host.implementation_binding"),
        )
    execution: BindingExecution
    if "provider_hosted" in families:
        execution = "provider_hosted"
    elif "remote" in families:
        execution = "remote"
    else:
        execution = "host"
    return BindingResolution(execution, MappingSupport("unsupported", None))


def _locator_families(locator: Mapping[str, object]) -> set[str]:
    keys = set(locator)
    families: set[str] = set()
    if keys & {"python", "typescript", "module"}:
        families.add("host")
    if keys & {"provider", "provider_tool", "tool"}:
        families.add("provider_hosted")
    if keys & {"endpoint", "url", "remote", "mcp"}:
        families.add("remote")
    return families


def _resolve_grants(
    ir: CanonicalIR,
    bindings: Mapping[SemanticId, BindingPlan],
    target: TargetBinding,
    capabilities: PlannerCapabilities,
    issues: list[PlanningIssue],
    obligations: dict[tuple[str, SemanticId | None], HostObligationPlan],
    event_types: set[str],
) -> tuple[dict[SemanticId, GrantMappingPlan], dict[SemanticId, MappingSupport]]:
    result: dict[SemanticId, GrantMappingPlan] = {}
    approval_support: dict[SemanticId, MappingSupport] = {}
    for grant_id, grant in ir.grants.items():
        failure_detail: str | None = None
        if grant.availability == "denied":
            outcome: MappingOutcome = "exact"
            mechanism: str | None = "contract.capability_denial"
            if grant.authorization == "approval_required":
                approval_support[grant_id] = MappingSupport(
                    "exact",
                    "contract.capability_denial",
                )
        else:
            binding = bindings.get(grant.capability_id)
            if binding is None:
                continue
            outcome = binding.outcome
            mechanism = binding.mechanism
            named_environment = (
                grant.execution is not None
                and grant.execution not in {"host", "provider_hosted", "remote"}
                and grant.execution in target.environments
            )
            if grant.execution in {"host", "provider_hosted", "remote"}:
                if grant.execution != binding.execution:
                    outcome = "degraded"
                    failure_detail = (
                        f"binding execution `{binding.execution}` does not satisfy "
                        f"`{grant.execution}`"
                    )
            elif named_environment:
                outcome = _combine_outcomes(outcome, "host_enforced")
                mechanism = f"{mechanism}+environment:{grant.execution}"
            elif grant.execution is not None:
                outcome = "unsupported"
                failure_detail = f"target environment `{grant.execution}` is not declared"
            if capabilities.mapping_resolver is not None:
                contextual = capabilities.mapping_resolver.grant_support(
                    grant=grant,
                    binding=binding,
                    named_environment=named_environment,
                )
                if contextual is not None:
                    outcome = _combine_outcomes(outcome, contextual.outcome)
                    mechanism = _combine_mechanisms(mechanism, contextual.mechanism)
                    if contextual.outcome in {"degraded", "unsupported"}:
                        failure_detail = (
                            "adapter grant support is unavailable for the requested "
                            "execution or isolation context"
                        )
                    _consume_support(contextual, grant_id, obligations, event_types)
            if grant.authorization == "approval_required":
                support = capabilities.approval
                if capabilities.mapping_resolver is not None:
                    support = (
                        capabilities.mapping_resolver.approval_support(
                            grant=grant,
                            binding=binding,
                        )
                        or support
                    )
                approval_support[grant_id] = support
                outcome = _combine_outcomes(outcome, support.outcome)
                mechanism = _combine_mechanisms(mechanism, support.mechanism)
                if support.outcome in {"degraded", "unsupported"}:
                    failure_detail = (
                        "adapter approval support is unavailable for "
                        f"`{binding.execution}` execution"
                    )
                _consume_support(support, grant_id, obligations, event_types)
        _require_mapping(
            outcome,
            grant_id,
            "grant",
            issues,
            detail=failure_detail or mechanism,
        )
        result[grant_id] = GrantMappingPlan(
            id=grant_id,
            agent_id=grant.agent_id,
            capability_id=grant.capability_id,
            availability=grant.availability,
            authorization=grant.authorization,
            execution=grant.execution,
            isolation_id=grant.isolation_id,
            outcome=outcome,
            mechanism=mechanism,
        )
    return result, approval_support


def _resolve_composition(
    ir: CanonicalIR,
    capabilities: PlannerCapabilities,
    issues: list[PlanningIssue],
    obligations: dict[tuple[str, SemanticId | None], HostObligationPlan],
    event_types: set[str],
) -> dict[SemanticId, CompositionMappingPlan]:
    result: dict[SemanticId, CompositionMappingPlan] = {}
    for edge_id, edge in ir.composition.items():
        contextual_failure = False
        support = capabilities.composition.get(
            f"{edge.mode}:{edge.history}",
            capabilities.composition.get(edge.mode, MappingSupport("unsupported", None)),
        )
        if capabilities.mapping_resolver is not None:
            contextual = capabilities.mapping_resolver.composition_support(
                edge=edge,
                declared=support,
            )
            if contextual is not None:
                support = contextual
                contextual_failure = support.outcome in {"degraded", "unsupported"}
        _require_mapping(
            support.outcome,
            edge_id,
            "composition edge",
            issues,
            detail=(
                "adapter composition support is unavailable for the requested isolation context"
                if contextual_failure
                else None
            ),
        )
        _consume_support(support, edge_id, obligations, event_types)
        result[edge_id] = CompositionMappingPlan(
            id=edge_id,
            source_agent_id=edge.source_agent_id,
            target_agent_id=edge.target_agent_id,
            mode=edge.mode,
            description=edge.description,
            history=edge.history,
            input_mappings=edge.input_mappings,
            audience=edge.audience,
            isolation_id=edge.isolation_id,
            outcome=support.outcome,
            mechanism=support.mechanism,
        )
    return result


def _resolve_controls(
    ir: CanonicalIR,
    agents: Mapping[SemanticId, AgentPlan],
    bindings: Mapping[SemanticId, BindingPlan],
    grants: Mapping[SemanticId, GrantMappingPlan],
    composition: Mapping[SemanticId, CompositionMappingPlan],
    capabilities: PlannerCapabilities,
    approval_support: Mapping[SemanticId, MappingSupport],
    issues: list[PlanningIssue],
    obligations: dict[tuple[str, SemanticId | None], HostObligationPlan],
    event_types: set[str],
) -> dict[SemanticId, ControlMappingPlan]:
    result: dict[SemanticId, ControlMappingPlan] = {}
    canonical_grants = ir.grants
    for control_id, control in ir.controls.items():
        derived_grant = (
            canonical_grants.get(control.derived_from)
            if control.derived_from is not None
            else None
        )
        if derived_grant is not None and derived_grant.authorization == "approval_required":
            support = approval_support.get(derived_grant.id, capabilities.approval)
        elif control.assessment == "static":
            support = MappingSupport("exact", "contract4agents.static_control")
        elif control.assessment == "post_run":
            support = MappingSupport("emulated", "contract4agents.post_run_control")
        else:
            support = capabilities.controls.get(control.assessment, MappingSupport("unsupported", None))
        if capabilities.mapping_resolver is not None:
            contextual_resolver = getattr(
                capabilities.mapping_resolver,
                "control_support",
                None,
            )
            if contextual_resolver is not None:
                tool_bindings = tuple(
                    sorted(
                        (
                            bindings[grant.capability_id]
                            for grant in grants.values()
                            if grant.agent_id == control.agent_id
                            and grant.availability == "enabled"
                            and grant.capability_id.kind == "tool"
                            and grant.capability_id in bindings
                        ),
                        key=lambda item: str(item.id),
                    )
                )
                has_composition_tools = any(
                    edge.source_agent_id == control.agent_id
                    and edge.mode == "delegate"
                    and edge.outcome not in {"degraded", "unsupported"}
                    for edge in composition.values()
                )
                contextual = contextual_resolver(
                    control=control,
                    agent=agents.get(control.agent_id),
                    has_tools=bool(tool_bindings) or has_composition_tools,
                    tool_bindings=tool_bindings,
                    declared=support,
                )
                if contextual is not None:
                    support = contextual
        if control.required:
            _require_mapping(support.outcome, control_id, "required control", issues)
        _consume_support(support, control_id, obligations, event_types)
        evidence = tuple(sorted(set(control.expected_evidence) | set(support.expected_event_types)))
        result[control_id] = ControlMappingPlan(
            id=control_id,
            required=control.required,
            assessment=control.assessment,
            outcome=support.outcome,
            mechanism=support.mechanism,
            expected_evidence=evidence,
        )
    return result


def _resolve_isolation(
    ir: CanonicalIR,
    target: TargetBinding,
    profile: TargetProfile,
    capabilities: PlannerCapabilities,
    issues: list[PlanningIssue],
    obligations: dict[tuple[str, SemanticId | None], HostObligationPlan],
    event_types: set[str],
) -> dict[SemanticId, IsolationMappingPlan]:
    if not ir.isolation_profiles:
        return {}
    environment = _selected_environment(target, profile, issues)
    if environment is None:
        return {}
    environment_entry = target.environments[environment]
    provider_value = environment_entry.values.get("provider")
    if not isinstance(provider_value, str):
        issues.append(PlanningIssue("PLN008", f"Environment `{environment}` has no provider identity"))
        return {}
    result: dict[SemanticId, IsolationMappingPlan] = {}
    for isolation_id, isolation in ir.isolation_profiles.items():
        dimensions: list[tuple[str, IsolationDimensionPlan]] = []
        for dimension, requested in _isolation_dimensions(isolation):
            support = _isolation_support(capabilities, dimension, requested, environment)
            _require_mapping(support.outcome, isolation_id, f"isolation dimension `{dimension}`", issues)
            _consume_support(support, isolation_id, obligations, event_types)
            dimensions.append(
                (dimension, IsolationDimensionPlan(requested, support.outcome, support.mechanism))
            )
        result[isolation_id] = IsolationMappingPlan(
            id=isolation_id,
            environment=environment,
            provider=provider_value,
            dimensions=FrozenMap(dimensions),
        )
    return result


def _selected_environment(
    target: TargetBinding,
    profile: TargetProfile,
    issues: list[PlanningIssue],
) -> str | None:
    selected = profile.options.get("environment")
    if selected is not None:
        if not isinstance(selected, str) or selected not in target.environments:
            issues.append(PlanningIssue("PLN008", f"Profile environment `{selected}` is not bound"))
            return None
        return selected
    if "in_process" in target.environments:
        return "in_process"
    if len(target.environments) == 1:
        return next(iter(target.environments))
    issues.append(PlanningIssue("PLN008", "Isolation requires one selected target environment"))
    return None


def _isolation_dimensions(profile: IsolationProfileIR) -> tuple[tuple[str, str], ...]:
    values = (
        ("context", profile.context),
        ("capabilities", profile.capabilities),
        ("state", profile.state),
        ("filesystem", profile.filesystem),
        ("network", profile.network),
        ("secrets", profile.secrets),
        ("return", profile.return_channel),
    )
    return tuple((name, value) for name, value in values if value is not None)


def _isolation_support(
    capabilities: PlannerCapabilities,
    dimension: str,
    requested: str,
    environment: str,
) -> MappingSupport:
    support = capabilities.isolation.get(f"{dimension}:{requested}")
    if support is not None:
        return support
    inherited = {
        ("context", "inherited"),
        ("capabilities", "inherited"),
        ("state", "shared"),
        ("filesystem", "inherited"),
        ("network", "inherited"),
        ("secrets", "inherited"),
        ("return", "full_trace"),
    }
    if (dimension, requested) in inherited:
        return MappingSupport("exact", f"{environment}.inherited")
    return MappingSupport("unsupported", None)


def _require_mapping(
    outcome: MappingOutcome,
    semantic_id: SemanticId,
    label: str,
    issues: list[PlanningIssue],
    *,
    detail: str | None = None,
) -> None:
    if outcome in {"degraded", "unsupported"}:
        suffix = f": {detail}" if detail else ""
        issues.append(
            PlanningIssue(
                "PLN009",
                f"Required {label} maps as `{outcome}`{suffix}",
                semantic_id,
            )
        )


def _consume_support(
    support: MappingSupport,
    semantic_id: SemanticId,
    obligations: dict[tuple[str, SemanticId | None], HostObligationPlan],
    event_types: set[str],
) -> None:
    # Mapping-specific evidence is conditional on that mapping being exercised.
    # Keep it on the mapping/control plan; the run-level completeness set contains
    # only unconditional lifecycle boundary events. Otherwise an unused approved
    # tool or handoff would make an otherwise complete run permanently unverified.
    if support.host_obligation:
        _add_obligation(
            obligations,
            HostObligationPlan("host.enforce_mapping", support.host_obligation, semantic_id),
        )


def _add_obligation(
    obligations: dict[tuple[str, SemanticId | None], HostObligationPlan],
    obligation: HostObligationPlan,
) -> None:
    obligations[(obligation.code, obligation.semantic_id)] = obligation


def _combine_outcomes(first: MappingOutcome, second: MappingOutcome) -> MappingOutcome:
    return first if _OUTCOME_RANK[first] >= _OUTCOME_RANK[second] else second


def _combine_mechanisms(first: str | None, second: str | None) -> str | None:
    if first and second:
        return f"{first}+{second}"
    return first or second


__all__ = ["plan_materialization"]
