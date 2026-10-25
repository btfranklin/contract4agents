"""Planning support for the bounded Bedrock AgentCore Harness route."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path

from contract4agents.adapters._native_names import native_name
from contract4agents.diagnostics import Diagnostic
from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    CapabilityIR,
    CompositionEdgeIR,
    ControlIR,
    GrantIR,
)
from contract4agents.planning import (
    AgentPlan,
    BindingKind,
    BindingPlan,
    BindingResolution,
    MappingSupport,
    PlannerCapabilities,
    describe_locator,
)
from contract4agents.target_bindings import BindingEntry, BindingSection, TargetBinding

_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_HARNESS_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}\Z")
_ROLE_ARN = re.compile(r"arn:aws(?:-[^:]+)?:iam::[0-9]{12}:role/.+\Z")
_INPUT_PATH = re.compile(r"input(?:\.[A-Za-z_][A-Za-z0-9_]*)+\Z")
_PROFILE_OPTIONS = frozenset(
    {
        "api_format",
        "execution_role_arn",
        "harness_name",
        "max_iterations",
        "max_tokens",
        "timeout_seconds",
        "temperature",
        "top_p",
        "output_mode",
    }
)
_RESERVED_TOOL_NAMES = frozenset({"shell", "file_operations"})
_MAX_INLINE_TOOLS = 64


class AgentCoreHarnessMappingResolver:
    """Resolve only the first supported Harness adapter route."""

    def resolve_binding(
        self,
        *,
        kind: BindingKind,
        locator: Mapping[str, object],
    ) -> BindingResolution:
        description = describe_locator(locator)
        if kind == "tool" and description.is_python_binding:
            return BindingResolution(
                "host",
                MappingSupport(
                    "exact",
                    "host.implementation_binding",
                    host_obligation=(
                        "The application must authenticate and authorize each caller, "
                        "preserve session identity, do not grant InvokeAgentRuntimeCommand "
                        "to callers, verify deployment and endpoint version "
                        "stability, and avoid hidden retries or fallback."
                    ),
                ),
            )
        return BindingResolution(description.execution, MappingSupport("unsupported", None))

    def binding_support(
        self,
        *,
        ir: CanonicalIR,
        capability: CapabilityIR | None,
        kind: BindingKind,
        locator: Mapping[str, object],
        declared: BindingResolution,
    ) -> BindingResolution | None:
        del ir, capability, kind, locator, declared
        return None

    def grant_support(
        self,
        *,
        grant: GrantIR,
        binding: BindingPlan,
        named_environment: bool,
    ) -> MappingSupport | None:
        if binding.kind == "tool" and (grant.isolation_id is not None or named_environment):
            return MappingSupport("unsupported", None)
        return None

    def approval_support(
        self,
        *,
        grant: GrantIR,
        binding: BindingPlan,
    ) -> MappingSupport | None:
        del grant
        if binding.kind == "tool" and binding.execution == "host":
            return MappingSupport(
                "emulated",
                "host.application_approval_and_tool_result_continuation",
                expected_event_types=(
                    "approval.requested",
                    "approval.completed",
                    "tool.started",
                    "tool.completed",
                ),
                host_obligation=(
                    "The application must approve each requested tool call and return "
                    "the tool result to the Harness continuation."
                ),
            )
        return MappingSupport("unsupported", None)

    def composition_support(
        self,
        *,
        edge: CompositionEdgeIR,
        declared: MappingSupport,
    ) -> MappingSupport | None:
        del declared
        if (
            edge.mode == "delegate"
            and edge.history == "none"
            and edge.isolation_id is None
            and all(
                isinstance(expression, str) and _INPUT_PATH.fullmatch(expression)
                for expression in edge.input_mappings.values()
            )
        ):
            return MappingSupport(
                "emulated",
                "host.application_delegate_dispatch",
                expected_event_types=(
                    "composition.started",
                    "composition.completed",
                ),
                host_obligation=(
                    "The application must dispatch the declared delegate call, validate "
                    "its typed arguments, and return its result to the Harness."
                ),
            )
        return MappingSupport("unsupported", None)

    def control_support(
        self,
        *,
        control: ControlIR,
        agent: AgentPlan | None,
        has_tools: bool,
        tool_bindings: tuple[BindingPlan, ...],
        declared: MappingSupport,
    ) -> MappingSupport | None:
        del tool_bindings, declared
        if control.assessment != "adapter":
            return None
        if control.derived_from != control.agent_id:
            if control.derived_from is not None and control.derived_from.kind == "grant":
                return None
            return MappingSupport("unsupported", None)
        if agent is None or not has_tools:
            return MappingSupport("unsupported", None)
        return MappingSupport(
            "host_enforced",
            "host.client_output_validator",
            expected_event_types=("output.accepted", "output.schema_failed"),
            host_obligation=(
                "The application must validate final Harness text as the declared JSON "
                "output type. Harness output mode does not enforce the schema."
            ),
        )


_MAPPING_RESOLVER = AgentCoreHarnessMappingResolver()


def agentcore_harness_target_binding_validator(
    target_name: str,
    section: BindingSection,
    name: str,
    entry: BindingEntry,
) -> tuple[Diagnostic, ...]:
    """Accept only Python host tools with an optional Harness tool name."""

    label = f"targets.{target_name}.{section}.{name}"
    values = entry.values
    if section != "tools":
        return (
            Diagnostic(
                "TGT111",
                f"AgentCore Harness binding `{label}` is not supported",
                hint="The first route supports Python host tools only.",
            ),
        )
    if set(values) - {"python", "tool_name"}:
        return (
            Diagnostic(
                "TGT111",
                f"AgentCore Harness binding `{label}` has unsupported keys",
                hint="Use `python` and, optionally, `tool_name`.",
            ),
        )
    if not describe_locator(values).is_python_binding:
        return (
            Diagnostic(
                "TGT111",
                f"AgentCore Harness binding `{label}` must use a Python host tool",
                hint="Remote, MCP, Gateway, provider-hosted, and non-Python tools are not supported.",
            ),
        )
    if "tool_name" in values:
        tool_name = values["tool_name"]
        if (
            not isinstance(tool_name, str)
            or _TOOL_NAME.fullmatch(tool_name) is None
            or tool_name in _RESERVED_TOOL_NAMES
            or "*" in tool_name
            or "@" in tool_name
        ):
            return (
                Diagnostic(
                    "TGT111",
                    f"AgentCore Harness binding `{label}` has an invalid tool name",
                    hint=(
                        "Use 1 to 64 letters, digits, underscores, or hyphens. Do not use a reserved name or wildcard."
                    ),
                ),
            )
    return ()


def agentcore_harness_target_profile_validator(
    ir: CanonicalIR,
    target_name: str,
    target: TargetBinding,
    project_root: Path,
) -> tuple[Diagnostic, ...]:
    """Check profile settings and reject context or isolation beyond this route."""

    del project_root
    diagnostics: list[Diagnostic] = []
    if ir.contexts or ir.external_contexts:
        diagnostics.append(
            Diagnostic(
                "TGT111",
                f"AgentCore Harness target `{target_name}` does not support declared context",
                hint="Pass typed agent input through the application. Remove declared context sources.",
            )
        )
    if any(capability.kind == "datasource" for capability in ir.capabilities.values()):
        diagnostics.append(
            Diagnostic(
                "TGT111",
                f"AgentCore Harness target `{target_name}` does not support datasources",
            )
        )
    if ir.isolation_profiles:
        diagnostics.append(
            Diagnostic(
                "TGT111",
                f"AgentCore Harness target `{target_name}` does not support isolation profiles",
                hint="Remove explicit isolation profiles for this route.",
            )
        )

    for profile_name, profile in target.profiles.items():
        _validate_options(
            profile.options,
            f"targets.{target_name}.profiles.{profile_name}.options",
            diagnostics,
        )
        for agent_name, configured_agent_profile in profile.agents.items():
            _validate_options(
                configured_agent_profile.options,
                f"targets.{target_name}.profiles.{profile_name}.agents.{agent_name}.options",
                diagnostics,
            )
        for agent in ir.agents.values():
            resolved_agent_profile = profile.agents.get(agent.name)
            options = dict(profile.options)
            if resolved_agent_profile is not None:
                options.update(resolved_agent_profile.options)
            if not _is_valid_role_arn(options.get("execution_role_arn")):
                diagnostics.append(
                    Diagnostic(
                        "TGT113",
                        f"AgentCore Harness profile `{profile_name}` requires a valid "
                        f"execution_role_arn for `{agent.name}`",
                        hint="Use an IAM role ARN such as `arn:aws:iam::123456789012:role/AgentRole`.",
                    )
                )
            enabled_tool_ids = {
                grant.capability_id
                for grant in ir.grants.values()
                if grant.agent_id == agent.id and grant.availability == "enabled" and grant.capability_id.kind == "tool"
            }
            delegate_edges = tuple(
                edge
                for edge in ir.composition.values()
                if edge.source_agent_id == agent.id
                and edge.mode == "delegate"
                and edge.history == "none"
                and edge.isolation_id is None
                and all(
                    isinstance(expression, str) and _INPUT_PATH.fullmatch(expression)
                    for expression in edge.input_mappings.values()
                )
            )
            if not enabled_tool_ids and not delegate_edges:
                diagnostics.append(
                    Diagnostic(
                        "TGT111",
                        f"AgentCore Harness agent `{agent.name}` has no enabled tool",
                        hint="Add an enabled Python tool or a delegate with no history.",
                    )
                )
            elif len(enabled_tool_ids) + len(delegate_edges) > _MAX_INLINE_TOOLS:
                diagnostics.append(
                    Diagnostic(
                        "TGT111",
                        f"AgentCore Harness agent `{agent.name}` has more than {_MAX_INLINE_TOOLS} enabled tools",
                    )
                )
            diagnostics.extend(
                _validate_agent_tool_names(
                    ir,
                    target,
                    agent,
                    delegate_edges,
                )
            )

        effective_harness_names: dict[str, str] = {}
        for agent in ir.agents.values():
            resolved_agent_profile = profile.agents.get(agent.name)
            options = dict(profile.options)
            if resolved_agent_profile is not None:
                options.update(resolved_agent_profile.options)
            effective_name = options.get("harness_name")
            if effective_name is None:
                effective_name = native_name("agent", agent.id, max_length=40)
            if not isinstance(effective_name, str):
                continue
            previous_agent = effective_harness_names.get(effective_name)
            if previous_agent is not None:
                diagnostics.append(
                    Diagnostic(
                        "TGT111",
                        f"AgentCore Harness name `{effective_name}` is used by both "
                        f"`{previous_agent}` and `{agent.name}`",
                    )
                )
            else:
                effective_harness_names[effective_name] = agent.name

    return tuple(diagnostics)


def agentcore_harness_planner_capabilities() -> PlannerCapabilities:
    """Return planning claims for the application-mediated Harness route."""

    return PlannerCapabilities.create(
        adapter="agentcore_harness",
        version="1",
        approval=MappingSupport("unsupported", None),
        composition={
            "delegate:none": MappingSupport(
                "emulated",
                "host.application_delegate_dispatch",
                expected_event_types=("composition.started", "composition.completed"),
                host_obligation=(
                    "The application must dispatch each declared delegate call and return "
                    "its typed result to the Harness."
                ),
            ),
            "delegate:summary": MappingSupport("unsupported", None),
            "delegate:full": MappingSupport("unsupported", None),
            "handoff:none": MappingSupport("unsupported", None),
            "handoff:summary": MappingSupport("unsupported", None),
            "handoff:full": MappingSupport("unsupported", None),
        },
        controls={
            "adapter": MappingSupport(
                "host_enforced",
                "host.client_output_validator",
                expected_event_types=("output.accepted", "output.schema_failed"),
                host_obligation=("Validate final Harness text against the declared output type in the application."),
            ),
            "runtime": MappingSupport("exact", "contract4agents.runtime_assessor"),
            "host_attested": MappingSupport(
                "host_enforced",
                "contract4agents.host_attestation",
                host_obligation="Provide a signed or recorded host attestation.",
            ),
            "semantic": MappingSupport("emulated", "contract4agents.semantic_judge"),
            "advisory": MappingSupport("unsupported", None),
        },
        isolation={},
        expected_event_types=("agent.started", "agent.completed", "output.accepted"),
        mapping_resolver=_MAPPING_RESOLVER,
    )


def _validate_options(
    options: Mapping[str, object],
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    unknown = sorted(set(options) - _PROFILE_OPTIONS)
    for key in unknown:
        diagnostics.append(
            Diagnostic(
                "TGT003",
                f"Unknown key `{key}` in `{path}`",
                hint="Remove unsupported Harness options.",
            )
        )
    api_format = options.get("api_format")
    if api_format is not None and api_format != "converse_stream":
        diagnostics.append(Diagnostic("TGT113", f"`{path}.api_format` must be `converse_stream`"))
    output_mode = options.get("output_mode")
    if output_mode is not None and output_mode != "host_validation":
        diagnostics.append(Diagnostic("TGT113", f"`{path}.output_mode` must be `host_validation`"))
    role_arn = options.get("execution_role_arn")
    if role_arn is not None and not _is_valid_role_arn(role_arn):
        diagnostics.append(Diagnostic("TGT113", f"`{path}.execution_role_arn` must be a valid IAM role ARN"))
    harness_name = options.get("harness_name")
    if harness_name is not None and (
        not isinstance(harness_name, str) or _HARNESS_NAME.fullmatch(harness_name) is None
    ):
        diagnostics.append(
            Diagnostic(
                "TGT113",
                f"`{path}.harness_name` must match the AWS Harness name format",
            )
        )
    for key in ("max_iterations", "max_tokens", "timeout_seconds"):
        value = options.get(key)
        if value is not None and (type(value) is not int or value < 1):
            diagnostics.append(Diagnostic("TGT113", f"`{path}.{key}` must be a positive integer"))
    for key, upper in (("temperature", 2.0), ("top_p", 1.0)):
        value = options.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or not 0.0 <= value <= upper
        ):
            diagnostics.append(
                Diagnostic(
                    "TGT113",
                    f"`{path}.{key}` must be a finite number from 0 to {upper:g}",
                )
            )


def _is_valid_role_arn(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 2048 and _ROLE_ARN.fullmatch(value) is not None


def _validate_agent_tool_names(
    ir: CanonicalIR,
    target: TargetBinding,
    agent: AgentIR,
    delegate_edges: tuple[CompositionEdgeIR, ...],
) -> tuple[Diagnostic, ...]:
    names: dict[str, str] = {}
    diagnostics: list[Diagnostic] = []
    agent_id = agent.id
    for grant in ir.grants.values():
        if grant.agent_id != agent_id or grant.availability != "enabled" or grant.capability_id.kind != "tool":
            continue
        capability = ir.capabilities[grant.capability_id]
        entry = target.tools.get(capability.name)
        if entry is None:
            continue
        explicit_name = entry.values.get("tool_name")
        name = explicit_name if isinstance(explicit_name, str) else native_name("tool", capability.id)
        _record_name(name, f"tool `{capability.name}`", names, diagnostics)
    for edge in delegate_edges:
        _record_name(
            native_name("delegate", edge.id),
            f"delegate `{edge.name}`",
            names,
            diagnostics,
        )
    return tuple(diagnostics)


def _record_name(
    name: str,
    source: str,
    names: dict[str, str],
    diagnostics: list[Diagnostic],
) -> None:
    previous = names.get(name)
    if previous is not None:
        diagnostics.append(
            Diagnostic(
                "TGT111",
                f"AgentCore Harness tool name `{name}` collides between {previous} and {source}",
            )
        )
    else:
        names[name] = source


__all__ = [
    "AgentCoreHarnessMappingResolver",
    "agentcore_harness_planner_capabilities",
    "agentcore_harness_target_binding_validator",
    "agentcore_harness_target_profile_validator",
]
