"""Private SDK-hook session coordination shared by native adapters."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Self

from contract4agents.ir import CanonicalIR
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing._closure import (
    TraceClosureEvidence,
    TraceInstrumentationChannel,
)
from contract4agents.tracing._models import (
    NormalizedTrace,
    TraceEvent,
    TraceSemanticRefs,
)
from contract4agents.tracing._native_identities import (
    EMPTY_NATIVE_TRACE_IDENTITIES,
    NativeTraceIdentityMap,
)
from contract4agents.tracing._session import NormalizedTraceSessionCore
from contract4agents.tracing._sinks import NormalizedTraceSink


class NativeHookTraceRouterCore:
    """Process-lifetime activation and native identity state for hook adapters."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self._current_session: ContextVar[NativeHookTraceSession | None] = ContextVar(
            f"contract4agents_{provider}_session_{id(self)}",
            default=None,
        )
        self._identities = EMPTY_NATIVE_TRACE_IDENTITIES
        self._lock = threading.Lock()
        self._shutdown = False

    def register_graph(self, graph: object) -> None:
        identities = NativeTraceIdentityMap.from_graph(graph)
        with self._lock:
            if self._shutdown:
                raise RuntimeError(f"The {self.provider} trace router is shut down")
            self._identities = self._identities.merge(identities)

    @property
    def identities(self) -> NativeTraceIdentityMap:
        with self._lock:
            return self._identities

    @property
    def current_session(self) -> NativeHookTraceSession | None:
        return self._current_session.get()

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            self._identities = EMPTY_NATIVE_TRACE_IDENTITIES

    def ensure_open(self) -> None:
        with self._lock:
            if self._shutdown:
                raise RuntimeError(f"The {self.provider} trace router is shut down")

    def _activate(
        self,
        session: NativeHookTraceSession,
    ) -> Token[NativeHookTraceSession | None]:
        self.ensure_open()
        return self._current_session.set(session)

    def _deactivate(self, token: Token[NativeHookTraceSession | None]) -> None:
        self._current_session.reset(token)


class NativeHookTraceSession(NormalizedTraceSessionCore):
    """Common provider hook/plugin session with no SDK imports."""

    def __init__(
        self,
        router: NativeHookTraceRouterCore,
        ir: CanonicalIR,
        plan: MaterializationPlan,
        *,
        provider: str,
        session_name: str,
        provenance_source: str,
        captured_channels: Iterable[TraceInstrumentationChannel],
        run_id: str,
        thread_id: str | None = None,
        sink: NormalizedTraceSink | None = None,
        prior_trace: NormalizedTrace | None = None,
        prior_closure: TraceClosureEvidence | None = None,
    ) -> None:
        self.router = router
        super().__init__(
            ir,
            plan,
            provider=provider,
            session_name=session_name,
            provenance_source=provenance_source,
            captured_channels=captured_channels,
            run_id=run_id,
            thread_id=thread_id,
            sink=sink,
            prior_trace=prior_trace,
            prior_closure=prior_closure,
        )
        self._activation_token: Token[NativeHookTraceSession | None] | None = None
        self._provider_trace_context: ContextVar[tuple[str, ...]] = ContextVar(
            f"contract4agents_{provider}_provider_trace_{id(self)}",
            default=(),
        )
        self._provider_trace_counter = 0
        self._last_provider_trace_id: str | None = None

    def __enter__(self) -> Self:
        with self._lock:
            if self._closed:
                raise RuntimeError(
                    f"A closed {self._session_name} session cannot be re-entered"
                )
            if self._activation_token is not None:
                raise RuntimeError(
                    f"A {self._session_name} session cannot be entered more than once"
                )
            self._activation_token = self.router._activate(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        with self._lock:
            token = self._activation_token
            self._activation_token = None
        if token is not None:
            self.router._deactivate(token)
        self.close()

    def _begin_provider_run(self, trace_id: str | None = None) -> str:
        attempt = self._current_attempt()
        self._provider_trace_counter += 1
        selected_trace_id = trace_id or (
            f"{self.context.run_id}:{attempt.attempt_id}:"
            f"invocation:{self._provider_trace_counter}"
            if attempt is not None
            else (
                f"{self.context.run_id}:unbound:"
                f"{self._provider_trace_counter}"
            )
        )
        active = self._provider_trace_context.get()
        if selected_trace_id in active:
            selected_trace_id = (
                f"{selected_trace_id}:nested:{self._provider_trace_counter}"
            )
        accepted = self._start_provider_trace(
            selected_trace_id,
            unbound_reason="SDK invocation started without attempt identity.",
            unbound_provenance_source=self._provenance_source,
        )
        if not accepted:
            raise RuntimeError(f"The {self._session_name} session is closed")
        self._provider_trace_context.set((*active, selected_trace_id))
        self._last_provider_trace_id = selected_trace_id
        return selected_trace_id

    def _end_provider_run(self) -> None:
        trace_id = self._require_provider_trace_id()
        self._end_provider_trace(trace_id)
        active = self._provider_trace_context.get()
        self._provider_trace_context.set(active[:-1])

    def _require_provider_trace_id(self) -> str:
        active = self._provider_trace_context.get()
        if not active:
            raise RuntimeError("No provider run is active in this trace context")
        return active[-1]

    def _native_agent_semantic(self, native_agent: object | None) -> TraceSemanticRefs:
        agent_id = self.router.identities.agent_id(native_agent)
        return TraceSemanticRefs(agent_id=agent_id or self._maybe_current_agent_id())

    def _native_tool_semantic(
        self,
        native_tool: object | None,
        *,
        native_agent: object | None = None,
    ) -> tuple[str, TraceSemanticRefs]:
        fallback_agent = self.router.identities.agent_id(native_agent)
        return self.router.identities.tool_semantic(
            native_tool,
            self.ir,
            fallback_agent=fallback_agent or self._maybe_current_agent_id(),
        )

    def _record_native_event(
        self,
        *,
        event_type: str,
        semantic: TraceSemanticRefs,
        provider_identity: str,
        data: dict[str, object] | None = None,
        evidence_refs: Iterable[str] = (),
    ) -> TraceEvent:
        return self._record_provider_event(
            event_type=event_type,
            semantic=semantic,
            data=data,
            trace_id=self._require_provider_trace_id(),
            span_id=provider_identity,
            evidence_refs=evidence_refs,
            provenance_source=self._provenance_source,
        )

    def _record_adjacent_native_event(
        self,
        *,
        event_type: str,
        semantic: TraceSemanticRefs,
        provider_identity: str,
        data: dict[str, object] | None = None,
        evidence_refs: Iterable[str] = (),
    ) -> TraceEvent:
        active = self._provider_trace_context.get()
        return self._record_provider_event(
            event_type=event_type,
            semantic=semantic,
            data=data,
            trace_id=active[-1] if active else self._last_provider_trace_id,
            span_id=provider_identity,
            evidence_refs=evidence_refs,
            provenance_source=self._provenance_source,
        )

    def _complete_response_path(
        self,
        response_identity: str,
        *,
        reason: str,
    ) -> None:
        trace_id = self._require_provider_trace_id()
        self._mark_response_complete(
            response_ids=(response_identity,),
            evidence_refs=(f"provider:{self._provider}:{trace_id}:{response_identity}",),
            reason=reason,
        )

    def _attempt_binding_active(self) -> bool:
        return self._activation_token is not None


__all__ = ["NativeHookTraceRouterCore", "NativeHookTraceSession"]
