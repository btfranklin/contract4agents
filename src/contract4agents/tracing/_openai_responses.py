"""OpenAI provider-response normalization into portable trace evidence."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping

from contract4agents.ir import SemanticId, semantic_id
from contract4agents.planning import GrantMappingPlan, MaterializationPlan
from contract4agents.tracing._models import (
    ProviderCorrelation,
    RedactionMetadata,
    TraceAttempt,
    TraceEvent,
    TraceRunContext,
    TraceSemanticRefs,
)
from contract4agents.tracing._openai_utils import (
    batch_identity,
    field_text,
    field_value,
    locator_tool,
    provider_model,
    timestamp,
)
from contract4agents.tracing._sinks import NormalizedTraceSink

_OPENAI_SUPPORTED_PROVIDER_HOSTED_CALLS = {
    "web_search_call": "web_search",
}

_OPENAI_UNSUPPORTED_PROVIDER_HOSTED_CALLS = {
    "file_search_call": "file_search",
    "code_interpreter_call": "code_interpreter",
    "image_generation_call": "image_generation",
    "mcp_call": "mcp",
    "mcp_list_tools": "mcp_list_tools",
    "tool_search_call": "tool_search",
}

# These calls are dispatched outside the provider-hosted tool path. Their
# authoritative evidence comes from SDK spans or the host application, not
# model-response normalization.
_OPENAI_HOST_DISPATCHED_CALLS = frozenset(
    {
        "apply_patch_call",
        "computer_call",
        "custom_tool_call",
        "function_call",
        "local_shell_call",
        "shell_call",
    }
)

_MISSING = object()


def resolve_provider_tool_grant(
    plan: MaterializationPlan,
    *,
    agent_id: SemanticId,
    provider: str,
    tool: str,
) -> GrantMappingPlan:
    """Resolve exactly one enabled provider-hosted grant from planned locators."""

    agent_id.require_kind("agent")
    matches: list[GrantMappingPlan] = []
    for grant in plan.grants.values():
        if grant.agent_id != agent_id or grant.availability != "enabled":
            continue
        binding = plan.bindings.get(grant.capability_id)
        if (
            binding is None
            or binding.kind != "tool"
            or binding.execution != "provider_hosted"
        ):
            continue
        selected_tool = locator_tool(binding.locator)
        if binding.locator.get("provider") == provider and selected_tool == tool:
            matches.append(grant)
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one enabled provider-hosted grant for "
            f"`{agent_id}` and `{provider}:{tool}`; found {len(matches)}"
        )
    return matches[0]


def normalize_openai_response_events(
    plan: MaterializationPlan,
    responses: Iterable[object],
    *,
    agent: str | SemanticId,
    context: TraceRunContext,
    attempt: TraceAttempt | None = None,
    batch_id: str | None = None,
    sink: NormalizedTraceSink | None = None,
) -> tuple[TraceEvent, ...]:
    """Normalize provider-hosted calls from Agents SDK model responses.

    Only provider identity, status, model metadata, and correlation identifiers
    are retained. Provider prompts, actions, queries, and outputs are excluded.
    """

    agent_id = agent if isinstance(agent, SemanticId) else semantic_id("agent", agent)
    agent_id.require_kind("agent")
    response_items = tuple(responses)
    events: list[TraceEvent] = []
    response_identities = [
        field_text(response, "response_id")
        or field_text(response, "request_id")
        or f"index-{index}"
        for index, response in enumerate(response_items)
    ]
    selected_batch_id = batch_id or (
        attempt.attempt_id if attempt is not None else batch_identity(response_identities)
    )
    for response_index, response in enumerate(response_items):
        response_id = field_text(response, "response_id")
        request_id = field_text(response, "request_id")
        response_model = provider_model(response)
        output = field_value(response, "output")
        response_identity = response_identities[response_index]
        receipt_data: dict[str, object] = {
            "output_item_count": len(output) if isinstance(output, list | tuple) else 0,
            "response_identity": response_identity,
        }
        if attempt is not None:
            receipt_data["attempt"] = attempt.to_dict()
        if response_model is not None:
            receipt_data["provider_model"] = response_model
        receipt = TraceEvent(
            context=context,
            event_id=(
                f"openai:response-batch:{selected_batch_id}:response:{response_index}:"
                f"{response_identity}:normalized"
            ),
            parent_event_id=None,
            event_type="provider.response.normalized",
            timestamp=timestamp(field_value(response, "completed_at")),
            semantic=TraceSemanticRefs(agent_id=agent_id),
            data=receipt_data,
            provider=ProviderCorrelation("openai", run_id=response_id, request_id=request_id),
            evidence_refs=tuple(
                reference
                for reference in (
                    f"provider:openai:response:{response_id}" if response_id else None,
                    f"provider:openai:request:{request_id}" if request_id else None,
                )
                if reference is not None
            ),
            provenance={"source": "openai-agents-sdk-response-normalizer"},
            redaction=RedactionMetadata(),
        )
        events.append(receipt)
        if sink is not None:
            sink.emit(receipt)
        if not isinstance(output, list | tuple):
            continue
        for item_index, item in enumerate(output):
            item_type = field_text(item, "type")
            if item_type is None or item_type in _OPENAI_HOST_DISPATCHED_CALLS:
                continue
            provider_tool = _OPENAI_SUPPORTED_PROVIDER_HOSTED_CALLS.get(item_type)
            unsupported_tool = _OPENAI_UNSUPPORTED_PROVIDER_HOSTED_CALLS.get(item_type)
            if provider_tool is None:
                provider_tool = unsupported_tool
            unrecognized_call = provider_tool is None and item_type.endswith("_call")
            if provider_tool is None and not unrecognized_call:
                continue
            call_id = field_text(item, "id")
            identity = ":".join(
                part
                for part in (
                    response_id or request_id or str(response_index),
                    call_id or str(item_index),
                )
            )
            event_id = f"openai:hosted-tool:{identity}"
            data: dict[str, object] = {
                "provider_tool": (
                    f"openai.{provider_tool}"
                    if provider_tool is not None
                    else f"openai.unrecognized:{item_type}"
                )
            }
            if attempt is not None:
                data["attempt"] = attempt.to_dict()
            status = field_text(item, "status")
            if status is not None:
                data["status"] = status
            if response_model is not None:
                data["provider_model"] = response_model
            evidence_refs = tuple(
                reference
                for reference in (
                    f"provider:openai:response:{response_id}" if response_id else None,
                    f"provider:openai:call:{call_id}" if call_id else None,
                )
                if reference is not None
            )
            if unsupported_tool is not None:
                event_type = "capability.undeclared"
                semantic = TraceSemanticRefs(agent_id=agent_id)
                data["reason"] = (
                    f"OpenAI provider-hosted response call type `{item_type}` is not supported by this adapter"
                )
            elif unrecognized_call:
                event_type = "capability.undeclared"
                semantic = TraceSemanticRefs(agent_id=agent_id)
                data["reason"] = (
                    f"Unrecognized OpenAI response call type `{item_type}`; "
                    "provider-hosted execution cannot be ruled out"
                )
            else:
                assert provider_tool is not None
                try:
                    grant = resolve_provider_tool_grant(
                        plan,
                        agent_id=agent_id,
                        provider="openai",
                        tool=provider_tool,
                    )
                except ValueError as exc:
                    event_type = "capability.undeclared"
                    semantic = TraceSemanticRefs(agent_id=agent_id)
                    data["reason"] = str(exc)
                else:
                    if status in {"failed", "cancelled", "canceled", "incomplete"}:
                        event_type = "tool.failed"
                    elif status is not None and status not in {"completed", "succeeded"}:
                        event_type = "tool.started"
                    else:
                        event_type = "tool.completed"
                    semantic = TraceSemanticRefs(
                        agent_id=agent_id,
                        capability_id=grant.capability_id,
                        grant_id=grant.id,
                        isolation_id=grant.isolation_id,
                    )
            event = TraceEvent(
                context=context,
                event_id=event_id,
                parent_event_id=None,
                event_type=event_type,
                timestamp=timestamp(field_value(item, "completed_at")),
                semantic=semantic,
                data=data,
                provider=ProviderCorrelation(
                    "openai",
                    run_id=response_id,
                    request_id=request_id,
                ),
                evidence_refs=evidence_refs,
                provenance={"source": "openai-agents-sdk-model-response"},
                redaction=RedactionMetadata(),
            )
            events.append(event)
            if sink is not None:
                sink.emit(event)
    batch_data: dict[str, object] = {
        "batch_id": selected_batch_id,
        "response_count": len(response_items),
        "response_ids": response_identities,
    }
    if attempt is not None:
        batch_data["attempt"] = attempt.to_dict()
    batch = TraceEvent(
        context=context,
        event_id=f"openai:response-batch:{selected_batch_id}:normalized",
        parent_event_id=None,
        event_type="provider.response_batch.normalized",
        timestamp=time.time(),
        semantic=TraceSemanticRefs(agent_id=agent_id),
        data=batch_data,
        provider=ProviderCorrelation("openai"),
        evidence_refs=(f"contract4agents:openai:response-batch:{selected_batch_id}",),
        provenance={"source": "openai-agents-sdk-response-normalizer"},
        redaction=RedactionMetadata(),
    )
    events.append(batch)
    if sink is not None:
        sink.emit(batch)
    return tuple(events)


def normalize_openai_exception_responses(
    plan: MaterializationPlan,
    exception: BaseException,
    *,
    agent: str | SemanticId,
    context: TraceRunContext,
    attempt: TraceAttempt | None = None,
    batch_id: str | None = None,
    sink: NormalizedTraceSink | None = None,
) -> tuple[TraceEvent, ...]:
    """Normalize model responses retained on an Agents SDK run exception.

    The helper deliberately records only provider response evidence. The host
    still owns exception handling, retry decisions, and lifecycle failure
    events.
    """

    run_data = getattr(exception, "run_data", None)
    if run_data is None:
        return ()
    raw_responses = getattr(run_data, "raw_responses", _MISSING)
    if raw_responses is _MISSING:
        return ()
    if not isinstance(raw_responses, Iterable) or isinstance(raw_responses, str | bytes | Mapping):
        raise TypeError("Agents SDK exception run_data.raw_responses must be an iterable of responses")
    return normalize_openai_response_events(
        plan,
        raw_responses,
        agent=agent,
        context=context,
        attempt=attempt,
        batch_id=batch_id,
        sink=sink,
    )

__all__ = [
    "normalize_openai_exception_responses",
    "normalize_openai_response_events",
    "resolve_provider_tool_grant",
]
