"""Strands hook events normalized into contract-bound trace evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from contract4agents.ir import CanonicalIR
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing._closure import (
    TraceClosureEvidence,
    TraceInstrumentationChannel,
)
from contract4agents.tracing._models import (
    NormalizedTrace,
    TraceEvent,
)
from contract4agents.tracing._native_session import (
    NativeHookTraceRouterCore,
    NativeHookTraceSession,
)
from contract4agents.tracing._provider_evidence import (
    ProviderOutcome,
    ProviderOutcomeCategory,
    ProviderOutcomeEvidence,
    ProviderOutcomePhase,
    ProviderUsageEvidence,
)
from contract4agents.tracing._sinks import NormalizedTraceSink

_STRANDS_CAPTURED_CHANNELS: frozenset[TraceInstrumentationChannel] = frozenset(
    {
        "agent",
        "approval",
        "composition",
        "output",
        "provider_response",
        "tool",
    }
)


class StrandsNormalizedTraceRouter(NativeHookTraceRouterCore):
    """Attach one lazy Strands hook bridge and route active host attempts."""

    def __init__(self) -> None:
        super().__init__("strands")
        self._attached_agents: set[int] = set()

    def open_session(
        self,
        ir: CanonicalIR,
        plan: MaterializationPlan,
        *,
        run_id: str,
        thread_id: str | None = None,
        sink: NormalizedTraceSink | None = None,
        prior_trace: NormalizedTrace | None = None,
        prior_closure: TraceClosureEvidence | None = None,
    ) -> StrandsNormalizedTraceSession:
        self.ensure_open()
        return StrandsNormalizedTraceSession(
            self,
            ir,
            plan,
            run_id=run_id,
            thread_id=thread_id,
            sink=sink,
            prior_trace=prior_trace,
            prior_closure=prior_closure,
        )

    def attach(self, graph: object) -> object:
        """Attach public Strands hooks to every native agent in one graph."""

        self.register_graph(graph)
        event_types = _load_hook_event_types()
        agents = getattr(graph, "agents", None)
        if not isinstance(agents, Mapping):
            raise TypeError("Native graph `agents` must be a mapping")
        bridge = _StrandsHookBridge(self)
        for agent in agents.values():
            if id(agent) in self._attached_agents:
                continue
            add_hook = getattr(agent, "add_hook", None)
            if not callable(add_hook):
                raise TypeError("Strands native agents must expose add_hook")
            add_hook(bridge.before_invocation, event_types["before_invocation"])
            add_hook(bridge.after_invocation, event_types["after_invocation"])
            add_hook(bridge.before_model, event_types["before_model"])
            add_hook(bridge.after_model, event_types["after_model"])
            add_hook(bridge.before_tool, event_types["before_tool"])
            add_hook(bridge.after_tool, event_types["after_tool"])
            self._attached_agents.add(id(agent))
        return bridge


class StrandsNormalizedTraceSession(NativeHookTraceSession):
    """Disposable normalized-evidence state for one logical Strands run."""

    def __init__(
        self,
        router: StrandsNormalizedTraceRouter,
        ir: CanonicalIR,
        plan: MaterializationPlan,
        *,
        run_id: str,
        thread_id: str | None = None,
        sink: NormalizedTraceSink | None = None,
        prior_trace: NormalizedTrace | None = None,
        prior_closure: TraceClosureEvidence | None = None,
    ) -> None:
        super().__init__(
            router,
            ir,
            plan,
            provider="strands",
            session_name="Strands trace",
            provenance_source="strands-agents-sdk-hook",
            captured_channels=_STRANDS_CAPTURED_CHANNELS,
            run_id=run_id,
            thread_id=thread_id,
            sink=sink,
            prior_trace=prior_trace,
            prior_closure=prior_closure,
        )
        self._native_sequence = 0
        self._reported_failure_keys: set[str] = set()

    def record_approval(
        self,
        *,
        native_tool: object,
        approved: bool,
        provider_identity: str | None = None,
    ) -> TraceEvent:
        """Record the result of a host-owned HITL decision."""

        _, semantic = self._native_tool_semantic(native_tool)
        return self._record_adjacent_native_event(
            event_type="approval.completed",
            semantic=semantic,
            provider_identity=provider_identity or self._next_identity("approval"),
            data={"approved": approved},
        )

    def record_approval_requested(
        self,
        *,
        native_tool: object,
        provider_identity: str | None = None,
    ) -> TraceEvent:
        """Record the approval pause surfaced to the host."""

        _, semantic = self._native_tool_semantic(native_tool)
        return self._record_adjacent_native_event(
            event_type="approval.requested",
            semantic=semantic,
            provider_identity=(
                provider_identity or self._next_identity("approval-request")
            ),
        )

    def _on_invocation_start(self, event: object) -> None:
        trace_id = _provider_identity(getattr(event, "invocation_state", None))
        self._begin_provider_run(trace_id)
        semantic = self._native_agent_semantic(getattr(event, "agent", None))
        self._record_native_event(
            event_type="agent.started",
            semantic=semantic,
            provider_identity=self._next_identity("invocation"),
        )

    def _on_invocation_end(self, event: object) -> None:
        semantic = self._native_agent_semantic(getattr(event, "agent", None))
        exception = getattr(event, "exception", None)
        result = getattr(event, "result", None)
        stop_reason = getattr(result, "stop_reason", None)
        identity = self._next_identity("invocation")
        if exception is None:
            self._record_native_event(
                event_type="provider.response.normalized",
                semantic=semantic,
                provider_identity=identity,
                data={"response_identity": identity},
            )
            self._complete_response_path(
                identity,
                reason="The Strands invocation lifecycle and result were captured.",
            )
            if stop_reason in {"interrupt", "checkpoint"}:
                self._record_native_event(
                    event_type="agent.interrupted",
                    semantic=semantic,
                    provider_identity=identity,
                    data={"stop_reason": stop_reason},
                )
            elif (
                self._current_attempt() is not None
                and semantic.agent_id == self._current_agent_id()
            ):
                evidence_refs = (
                    f"provider:strands:{self._require_provider_trace_id()}:{identity}",
                )
                if getattr(result, "structured_output", None) is not None:
                    self.record_output_accepted(
                        agent=self._current_agent_id(),
                        evidence_refs=evidence_refs,
                    )
                else:
                    self.record_output_schema_failure(
                        agent=self._current_agent_id(),
                        evidence_refs=evidence_refs,
                    )
                    self._mark_response_incomplete(
                        "The Strands AgentResult did not contain validated structured output."
                    )
            if stop_reason not in {"interrupt", "checkpoint"}:
                self._record_native_event(
                    event_type="agent.completed",
                    semantic=semantic,
                    provider_identity=identity,
                )
            # The public AgentResult shape always exposes metrics and
            # stop_reason.  Keep compatibility with minimal hook fakes that
            # predate provider evidence and expose neither.
            if getattr(result, "metrics", None) is not None or stop_reason is not None:
                attempt = self._current_attempt()
                state = self._attempts.get(attempt.attempt_id) if attempt is not None else None
                agent_id = state.agent_id if state is not None else semantic.agent_id
                if attempt is not None and agent_id is not None:
                    stop_outcome, stop_category, stop_phase = _strands_stop_outcome(stop_reason)
                    self.record_provider_outcome(
                        ProviderOutcomeEvidence(
                            agent_id=agent_id,
                            attempt_id=attempt.attempt_id,
                            invocation_id=attempt.invocation_id,
                            attempt_number=attempt.number,
                            phase=stop_phase,
                            outcome=stop_outcome,
                            category=stop_category,
                            state="observed",
                            classifier_provenance="strands.AgentResult.stop_reason",
                            response_id=identity,
                            response_received=True,
                        ),
                        provider_identity=identity,
                    )
                    self.record_provider_usage(
                        _strands_usage_evidence(
                            result,
                            agent_id=agent_id,
                            attempt=attempt,
                            identity=identity,
                        ),
                        provider_identity=identity,
                    )
        else:
            self._record_native_event(
                event_type="agent.failed",
                semantic=semantic,
                provider_identity=identity,
                data={"error": True},
            )
            self._mark_response_unverified(
                "The Strands invocation failed without a normalized result."
            )
            attempt = self._current_attempt()
            state = self._attempts.get(attempt.attempt_id) if attempt is not None else None
            agent_id = state.agent_id if state is not None else semantic.agent_id
            if attempt is not None and agent_id is not None:
                self.record_provider_outcome(
                    ProviderOutcomeEvidence(
                        agent_id=agent_id,
                        attempt_id=attempt.attempt_id,
                        invocation_id=attempt.invocation_id,
                        attempt_number=attempt.number,
                        phase="transport",
                        outcome="failed",
                        category="unknown",
                        state="observed",
                        classifier_provenance="strands.after_invocation.exception",
                        response_received=False,
                    ),
                )
                self.record_provider_usage(
                    ProviderUsageEvidence(
                        scope="attempt",
                        coverage="unavailable",
                        aggregation_identity=attempt.attempt_id,
                        aggregation_basis="one failed Strands Agent invocation",
                        provenance="strands.AgentResult.metrics.accumulated_usage",
                        agent_id=agent_id,
                        attempt_id=attempt.attempt_id,
                        invocation_id=attempt.invocation_id,
                    ),
                    provider_identity=identity,
                )
        self._end_provider_run()

    def _on_model_start(self, event: object) -> None:
        self._record_native_event(
            event_type="provider.response.started",
            semantic=self._native_agent_semantic(getattr(event, "agent", None)),
            provider_identity=self._next_identity("model"),
        )

    def _on_model_end(self, event: object) -> None:
        identity = self._next_identity("model")
        exception = getattr(event, "exception", None)
        if exception is None:
            self._record_native_event(
                event_type="provider.response.normalized",
                semantic=self._native_agent_semantic(getattr(event, "agent", None)),
                provider_identity=identity,
                data={"response_identity": identity},
            )
            self._complete_response_path(
                identity,
                reason="The Strands model response hook completed.",
            )
        else:
            self._record_native_event(
                event_type="provider.response.failed",
                semantic=self._native_agent_semantic(getattr(event, "agent", None)),
                provider_identity=identity,
                data={"error": True},
            )
            failure_key = self._require_provider_trace_id() + ":" + identity
            if failure_key not in self._reported_failure_keys:
                self._reported_failure_keys.add(failure_key)
                attempt = self._current_attempt()
                agent_id = (
                    self._native_agent_semantic(getattr(event, "agent", None)).agent_id
                    or self._maybe_current_agent_id()
                )
                if attempt is not None and agent_id is not None:
                    self.record_provider_outcome(
                        ProviderOutcomeEvidence(
                            agent_id=agent_id,
                            attempt_id=attempt.attempt_id,
                            invocation_id=attempt.invocation_id,
                            attempt_number=attempt.number,
                            phase="transport",
                            outcome="failed",
                            category="unknown",
                            state="observed",
                            classifier_provenance="strands.after_model.exception",
                            response_received=False,
                        ),
                        provider_identity=identity,
                    )
                    self.record_provider_usage(
                        ProviderUsageEvidence(
                            scope="model_call",
                            coverage="unavailable",
                            aggregation_identity=identity,
                            aggregation_basis="one failed Strands model hook",
                            provenance="strands.after_model.exception",
                            agent_id=agent_id,
                            attempt_id=attempt.attempt_id,
                            invocation_id=attempt.invocation_id,
                        ),
                        provider_identity=identity,
                    )
            self._mark_response_unverified(
                "The Strands model hook reported an exception without response evidence."
            )

    def _on_tool_start(self, event: object) -> None:
        native_tool = getattr(event, "selected_tool", None)
        if _is_structured_output_tool(native_tool):
            return
        kind, semantic = self._native_tool_semantic(
            native_tool or _tool_name(event),
            native_agent=getattr(event, "agent", None),
        )
        event_type = (
            f"{kind}.started"
            if kind == "composition" or semantic.capability_id is not None
            else "capability.undeclared"
        )
        self._record_native_event(
            event_type=event_type,
            semantic=semantic,
            provider_identity=_tool_use_identity(event) or self._next_identity(kind),
        )

    def _on_tool_end(self, event: object) -> None:
        native_tool = getattr(event, "selected_tool", None)
        if _is_structured_output_tool(native_tool):
            return
        kind, semantic = self._native_tool_semantic(
            native_tool or _tool_name(event),
            native_agent=getattr(event, "agent", None),
        )
        identity = _tool_use_identity(event) or self._next_identity(kind)
        result = getattr(event, "result", None)
        exception = getattr(event, "exception", None)
        if exception is None and isinstance(result, BaseException):
            exception = result
        suffix = "failed" if exception is not None else "completed"
        event_type = (
            f"{kind}.{suffix}"
            if kind == "composition" or semantic.capability_id is not None
            else "provider.tool.failed"
        )
        self._record_native_event(
            event_type=event_type,
            semantic=semantic,
            provider_identity=identity,
            data={"error": exception is not None},
        )

    def _next_identity(self, kind: str) -> str:
        self._native_sequence += 1
        return f"{kind}:{self._native_sequence}"


class _StrandsHookBridge:
    def __init__(self, router: StrandsNormalizedTraceRouter) -> None:
        self.router = router

    def before_invocation(self, event: Any) -> None:
        session = self.router.current_session
        if isinstance(session, StrandsNormalizedTraceSession):
            session._on_invocation_start(event)

    def after_invocation(self, event: Any) -> None:
        session = self.router.current_session
        if isinstance(session, StrandsNormalizedTraceSession):
            session._on_invocation_end(event)

    def before_model(self, event: Any) -> None:
        session = self.router.current_session
        if isinstance(session, StrandsNormalizedTraceSession):
            session._on_model_start(event)

    def after_model(self, event: Any) -> None:
        session = self.router.current_session
        if isinstance(session, StrandsNormalizedTraceSession):
            session._on_model_end(event)

    def before_tool(self, event: Any) -> None:
        session = self.router.current_session
        if isinstance(session, StrandsNormalizedTraceSession):
            session._on_tool_start(event)

    def after_tool(self, event: Any) -> None:
        session = self.router.current_session
        if isinstance(session, StrandsNormalizedTraceSession):
            session._on_tool_end(event)


def _load_hook_event_types() -> dict[str, type[object]]:
    try:
        from strands.hooks import (
            AfterInvocationEvent,
            AfterModelCallEvent,
            AfterToolCallEvent,
            BeforeInvocationEvent,
            BeforeModelCallEvent,
            BeforeToolCallEvent,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Strands tracing requires the `strands` extra; "
            "install `contract4agents[strands]`."
        ) from exc
    return {
        "after_invocation": AfterInvocationEvent,
        "after_model": AfterModelCallEvent,
        "after_tool": AfterToolCallEvent,
        "before_invocation": BeforeInvocationEvent,
        "before_model": BeforeModelCallEvent,
        "before_tool": BeforeToolCallEvent,
    }


def _provider_identity(value: object) -> str | None:
    if isinstance(value, Mapping):
        for key in ("trace_id", "invocation_id", "request_id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return None


def _tool_use_identity(event: object) -> str | None:
    tool_use = getattr(event, "tool_use", None)
    if isinstance(tool_use, Mapping):
        for key in ("toolUseId", "tool_use_id", "id"):
            candidate = tool_use.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return None


def _tool_name(event: object) -> str | None:
    tool_use = getattr(event, "tool_use", None)
    if isinstance(tool_use, Mapping):
        candidate = tool_use.get("name")
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


def _is_structured_output_tool(native_tool: object | None) -> bool:
    return getattr(native_tool, "tool_type", None) == "structured_output"


def _strands_usage_value(value: object, name: str) -> int | None:
    if isinstance(value, Mapping):
        candidate = value.get(name)
    else:
        candidate = getattr(value, name, None)
    return candidate if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0 else None


def _strands_usage_evidence(
    result: object,
    *,
    agent_id: object,
    attempt: object,
    identity: str,
) -> ProviderUsageEvidence:
    from contract4agents.ir import SemanticId
    from contract4agents.tracing._models import TraceAttempt

    if not isinstance(agent_id, SemanticId) or not isinstance(attempt, TraceAttempt):
        raise TypeError("Strands usage evidence requires normalized agent and attempt identities")
    metrics = getattr(result, "metrics", None)
    usage = getattr(metrics, "accumulated_usage", None)
    if usage is None:
        return ProviderUsageEvidence(
            scope="attempt",
            coverage="unavailable",
            aggregation_identity=attempt.attempt_id,
            aggregation_basis="one Strands AgentResult",
            provenance="strands.AgentResult.metrics.accumulated_usage",
            agent_id=agent_id,
            attempt_id=attempt.attempt_id,
            invocation_id=attempt.invocation_id,
        )
    input_tokens = _strands_usage_value(usage, "inputTokens")
    output_tokens = _strands_usage_value(usage, "outputTokens")
    total_tokens = _strands_usage_value(usage, "totalTokens")
    cached_input_tokens = _strands_usage_value(usage, "cacheReadInputTokens")
    if cached_input_tokens is not None and input_tokens is not None and cached_input_tokens > input_tokens:
        cached_input_tokens = None
    if not any(
        value is not None
        for value in (input_tokens, cached_input_tokens, output_tokens, total_tokens)
    ):
        coverage = "unavailable"
    elif input_tokens is not None and output_tokens is not None and total_tokens is not None:
        if total_tokens != input_tokens + output_tokens:
            total_tokens = None
            coverage = "partial"
        else:
            coverage = "complete"
    else:
        coverage = "partial"
    return ProviderUsageEvidence(
        scope="attempt",
        coverage=coverage,  # type: ignore[arg-type]
        aggregation_identity=attempt.attempt_id,
        aggregation_basis="Strands AgentResult.metrics.accumulated_usage",
        provenance="strands.AgentResult.metrics.accumulated_usage",
        request_count=None,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        agent_id=agent_id,
        attempt_id=attempt.attempt_id,
        invocation_id=attempt.invocation_id,
    )


def _strands_stop_outcome(
    stop_reason: object,
) -> tuple[ProviderOutcome, ProviderOutcomeCategory, ProviderOutcomePhase]:
    if stop_reason == "cancelled":
        return "cancelled", "cancelled", "cancellation"
    if stop_reason in {"content_filtered", "guardrail_intervened"}:
        return "refused", "refusal", "response"
    if stop_reason in {"interrupt", "checkpoint"}:
        # Host interruption/checkpoint is not a provider cancellation claim.
        return "unknown", "unknown", "response"
    return "succeeded", "transport", "response"


__all__ = ["StrandsNormalizedTraceRouter", "StrandsNormalizedTraceSession"]
