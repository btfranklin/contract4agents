"""Google ADK plugin callbacks normalized into contract-bound trace evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextvars import Token
from importlib import import_module
from types import TracebackType
from typing import Any, Self

from contract4agents.ir import CanonicalIR
from contract4agents.planning import MaterializationPlan
from contract4agents.tracing._closure import (
    TraceClosureEvidence,
    TraceInstrumentationChannel,
)
from contract4agents.tracing._models import NormalizedTrace, TraceEvent, TraceSemanticRefs
from contract4agents.tracing._native_session import (
    NativeHookTraceRouterCore,
    NativeHookTraceSession,
)
from contract4agents.tracing._provider_evidence import (
    ProviderOutcomeEvidence,
    ProviderUsageEvidence,
)
from contract4agents.tracing._sinks import NormalizedTraceSink

_GOOGLE_ADK_CAPTURED_CHANNELS: frozenset[TraceInstrumentationChannel] = frozenset(
    {
        "agent",
        "approval",
        "composition",
        "output",
        "provider_response",
        "tool",
    }
)


class GoogleADKNormalizedTraceRouter(NativeHookTraceRouterCore):
    """Coordinate one lazy ADK plugin with active host-owned attempts."""

    def __init__(self) -> None:
        super().__init__("google_adk")

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
    ) -> GoogleADKNormalizedTraceSession:
        self.ensure_open()
        return GoogleADKNormalizedTraceSession(
            self,
            ir,
            plan,
            run_id=run_id,
            thread_id=thread_id,
            sink=sink,
            prior_trace=prior_trace,
            prior_closure=prior_closure,
        )

    def attach(self, graph: object) -> GoogleADKNormalizedTraceRouter:
        """Register native identities before the host constructs its ADK Runner."""

        self.register_graph(graph)
        return self

    def plugin(self) -> object:
        """Create the ADK BasePlugin bridge lazily for host-owned App/Runner setup."""

        try:
            module = import_module("google.adk.plugins.base_plugin")
        except ImportError as exc:
            raise RuntimeError(
                "Google ADK tracing requires the `google-adk` extra; "
                "install `contract4agents[google-adk]`."
            ) from exc
        base_plugin = getattr(module, "BasePlugin", None)
        if not isinstance(base_plugin, type):
            raise RuntimeError("Google ADK does not expose BasePlugin")
        bridge = _GoogleADKPluginBridge(self)

        async def before_run_callback(
            plugin: object,
            *,
            invocation_context: object,
        ) -> None:
            del plugin
            bridge.before_run(invocation_context)

        async def after_run_callback(
            plugin: object,
            *,
            invocation_context: object,
        ) -> None:
            del plugin
            bridge.after_run(invocation_context)

        async def on_run_error_callback(
            plugin: object,
            *,
            invocation_context: object,
            error: Exception,
        ) -> None:
            del plugin
            bridge.run_error(invocation_context, error)

        async def before_agent_callback(
            plugin: object,
            *,
            agent: object,
            callback_context: object,
        ) -> None:
            del plugin
            bridge.before_agent(agent, callback_context)

        async def after_agent_callback(
            plugin: object,
            *,
            agent: object,
            callback_context: object,
        ) -> None:
            del plugin
            bridge.after_agent(agent, callback_context)

        async def on_agent_error_callback(
            plugin: object,
            *,
            agent: object,
            callback_context: object,
            error: Exception,
        ) -> None:
            del plugin
            bridge.agent_error(agent, callback_context, error)

        async def before_model_callback(
            plugin: object,
            *,
            callback_context: object,
            llm_request: object,
        ) -> None:
            del plugin
            bridge.before_model(callback_context, llm_request)

        async def after_model_callback(
            plugin: object,
            *,
            callback_context: object,
            llm_response: object,
        ) -> None:
            del plugin
            bridge.after_model(callback_context, llm_response)

        async def on_model_error_callback(
            plugin: object,
            *,
            callback_context: object,
            llm_request: object,
            error: Exception,
        ) -> None:
            del plugin
            bridge.model_error(callback_context, llm_request, error)

        async def before_tool_callback(
            plugin: object,
            *,
            tool: object,
            tool_args: dict[str, Any],
            tool_context: object,
        ) -> None:
            del plugin
            bridge.before_tool(tool, tool_args, tool_context)

        async def after_tool_callback(
            plugin: object,
            *,
            tool: object,
            tool_args: dict[str, Any],
            tool_context: object,
            result: dict[str, Any],
        ) -> None:
            del plugin
            bridge.after_tool(tool, tool_args, tool_context, result)

        async def on_tool_error_callback(
            plugin: object,
            *,
            tool: object,
            tool_args: dict[str, Any],
            tool_context: object,
            error: Exception,
        ) -> None:
            del plugin
            bridge.tool_error(tool, tool_args, tool_context, error)

        async def on_event_callback(
            plugin: object,
            *,
            invocation_context: object,
            event: object,
        ) -> None:
            del plugin
            bridge.on_event(invocation_context, event)

        plugin_type = type(
            "Contract4AgentsGoogleADKTracePlugin",
            (base_plugin,),
            {
                "after_agent_callback": after_agent_callback,
                "after_model_callback": after_model_callback,
                "after_run_callback": after_run_callback,
                "after_tool_callback": after_tool_callback,
                "before_agent_callback": before_agent_callback,
                "before_model_callback": before_model_callback,
                "before_run_callback": before_run_callback,
                "before_tool_callback": before_tool_callback,
                "on_agent_error_callback": on_agent_error_callback,
                "on_event_callback": on_event_callback,
                "on_model_error_callback": on_model_error_callback,
                "on_run_error_callback": on_run_error_callback,
                "on_tool_error_callback": on_tool_error_callback,
            },
        )
        return plugin_type(name=f"contract4agents_trace_{id(self)}")


class GoogleADKNormalizedTraceSession(NativeHookTraceSession):
    """Disposable normalized-evidence state for one logical ADK run."""

    def __init__(
        self,
        router: GoogleADKNormalizedTraceRouter,
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
            provider="google_adk",
            session_name="Google ADK trace",
            provenance_source="google-adk-plugin",
            captured_channels=_GOOGLE_ADK_CAPTURED_CHANNELS,
            run_id=run_id,
            thread_id=thread_id,
            sink=sink,
            prior_trace=prior_trace,
            prior_closure=prior_closure,
        )
        self._native_sequence = 0
        self._run_failed = False
        self._terminal_response_seen = False
        self._reported_response_keys: set[str] = set()
        self._output_validation_token: Token[Any] | None = None

    def __enter__(self) -> Self:
        super().__enter__()
        from contract4agents.materialization._google_adk import (
            _set_output_validation_observer,
        )

        self._output_validation_token = _set_output_validation_observer(
            self._observe_output_validation
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        token = self._output_validation_token
        self._output_validation_token = None
        if token is not None:
            from contract4agents.materialization._google_adk import (
                _reset_output_validation_observer,
            )

            _reset_output_validation_observer(token)
        super().__exit__(exc_type, exc, traceback)

    def record_approval(
        self,
        *,
        native_tool: object,
        approved: bool,
        provider_identity: str | None = None,
    ) -> TraceEvent:
        """Record one decision using the matching native function-call identity."""

        _, semantic = self._native_tool_semantic(native_tool)
        return self._record_adjacent_native_event(
            event_type="approval.completed",
            semantic=semantic,
            provider_identity=provider_identity or self._next_identity("confirmation"),
            data={"approved": approved},
        )

    def record_approval_requested(
        self,
        *,
        native_tool: object,
        provider_identity: str | None = None,
    ) -> TraceEvent:
        """Record one pause using the matching native function-call identity."""

        _, semantic = self._native_tool_semantic(native_tool)
        return self._record_adjacent_native_event(
            event_type="approval.requested",
            semantic=semantic,
            provider_identity=(
                provider_identity or self._next_identity("confirmation-request")
            ),
        )

    def _observe_output_validation(
        self,
        semantic_name: str,
        accepted: bool,
    ) -> None:
        if (
            self._current_attempt() is None
            or semantic_name != self._current_agent_id().parts[0]
        ):
            return
        evidence_refs = ("google-adk:terminal-schema-validation",)
        if accepted:
            self.record_output_accepted(
                agent=semantic_name,
                evidence_refs=evidence_refs,
            )
        else:
            self.record_output_schema_failure(
                agent=semantic_name,
                evidence_refs=evidence_refs,
            )
            self._mark_response_incomplete(
                "Google ADK terminal output failed provider-owned schema validation."
            )

    def _on_run_start(self, invocation_context: object) -> None:
        self._run_failed = False
        self._begin_provider_run(_adk_identity(invocation_context))

    def _on_run_end(
        self,
        invocation_context: object,
        *,
        error: bool,
        exception: BaseException | None = None,
    ) -> None:
        if not self._provider_trace_context.get():
            return
        identity = self._next_identity("run")
        if error:
            self._run_failed = True
            if not self._terminal_response_seen:
                self._record_native_event(
                    event_type="agent.failed",
                    semantic=TraceSemanticRefs(
                        agent_id=self._maybe_current_agent_id()
                    ),
                    provider_identity=identity,
                    data={"error": True},
                )
                self._report_adk_outcome(
                    response=None,
                    exception=exception,
                    callback_context=None,
                    identity=identity,
                )
        elif not self._run_failed:
            if not self._terminal_response_seen:
                semantic = TraceSemanticRefs(agent_id=self._maybe_current_agent_id())
                self._record_native_event(
                    event_type="provider.response.normalized",
                    semantic=semantic,
                    provider_identity=identity,
                    data={"response_identity": identity},
                )
                self._complete_response_path(
                    identity,
                    reason="The Google ADK run and terminal event lifecycle were captured.",
                )
                self._report_adk_outcome(
                    response=None,
                    exception=None,
                    callback_context=None,
                    identity=identity,
                )
        self._end_provider_run()

    def _on_agent_start(self, native_agent: object) -> None:
        self._record_native_event(
            event_type="agent.started",
            semantic=self._native_agent_semantic(native_agent),
            provider_identity=self._next_identity("agent"),
        )

    def _on_agent_end(self, native_agent: object, *, error: bool) -> None:
        if error:
            self._run_failed = True
        self._record_native_event(
            event_type="agent.failed" if error else "agent.completed",
            semantic=self._native_agent_semantic(native_agent),
            provider_identity=self._next_identity("agent"),
            data={"error": error},
        )

    def _on_model_start(self, callback_context: object) -> None:
        self._record_native_event(
            event_type="provider.response.started",
            semantic=_context_semantic(self, callback_context),
            provider_identity=self._next_identity("model"),
        )

    def _on_model_end(
        self,
        callback_context: object,
        *,
        error: bool,
        llm_response: object | None = None,
        exception: BaseException | None = None,
    ) -> None:
        identity = (
            _adk_identity(llm_response)
            or _adk_identity(callback_context)
            or self._next_identity("model")
        )
        response_key = f"{self._require_provider_trace_id()}:{identity}"
        if response_key in self._reported_response_keys:
            return
        self._reported_response_keys.add(response_key)
        if error:
            self._record_native_event(
                event_type="provider.response.failed",
                semantic=_context_semantic(self, callback_context),
                provider_identity=identity,
                data={"error": True},
            )
            self._report_adk_outcome(
                response=llm_response,
                exception=exception,
                callback_context=callback_context,
                identity=identity,
            )
            self._mark_response_unverified(
                "The Google ADK model callback failed without normalized response evidence."
            )
        else:
            self._record_native_event(
                event_type="provider.response.normalized",
                semantic=_context_semantic(self, callback_context),
                provider_identity=identity,
                data={"response_identity": identity},
            )
            self._complete_response_path(
                identity,
                reason="The Google ADK model response callback completed.",
            )
            self._report_adk_outcome(
                response=llm_response,
                exception=None,
                callback_context=callback_context,
                identity=identity,
            )
        self._terminal_response_seen = True

    def _report_adk_outcome(
        self,
        *,
        response: object | None,
        exception: BaseException | None,
        callback_context: object | None,
        identity: str,
    ) -> None:
        agent_id = self._maybe_current_agent_id()
        if agent_id is None:
            return
        error_code = _adk_safe_code(response) or _adk_safe_code(exception)
        http_status = _adk_status_code(response) or _adk_status_code(exception)
        explicit_refusal = _adk_refusal(response)
        interrupted = _adk_bool(response, "interrupted")
        if exception is not None:
            outcome = "failed"
            category, state = _adk_error_classification(exception)
            phase = "transport"
        elif explicit_refusal:
            outcome = "refused"
            category = "refusal"
            phase = "response"
            state = "observed"
        elif http_status in {401, 403, 429} or (http_status is not None and http_status >= 500):
            outcome = "failed"
            category, state = _adk_error_classification(response)
            phase = "transport"
        elif error_code is not None:
            outcome = "failed"
            category = "provider_error"
            phase = "response"
            state = "observed"
        elif interrupted:
            outcome = "unknown"
            category = "unknown"
            phase = "response"
            state = "observed"
        else:
            outcome = "succeeded"
            category = "transport"
            phase = "response"
            state = "observed"
        response_received = response is not None
        self.record_provider_outcome(
            ProviderOutcomeEvidence(
                agent_id=agent_id,
                attempt_id=self._require_attempt(None).attempt_id,
                invocation_id=self._require_attempt(None).invocation_id,
                attempt_number=self._require_attempt(None).number,
                phase=phase,  # type: ignore[arg-type]
                outcome=outcome,  # type: ignore[arg-type]
                category=category,  # type: ignore[arg-type]
                state=state,  # type: ignore[arg-type]
                classifier_provenance="google-adk.llm-response-public-fields",
                http_status=http_status,
                provider_error_code=error_code,
                response_id=identity,
                response_received=response_received,
            ),
            provider_identity=identity,
        )
        self.record_provider_usage(
            _adk_usage_evidence(
                response,
                agent_id=agent_id,
                attempt=self._require_attempt(None),
                identity=identity,
            ),
            provider_identity=identity,
        )

    def _on_tool(
        self,
        native_tool: object,
        tool_context: object,
        *,
        phase: str,
        error: bool,
    ) -> None:
        kind, semantic = self._native_tool_semantic(
            native_tool,
            native_agent=getattr(tool_context, "agent_name", None),
        )
        if kind == "tool" and semantic.capability_id is None:
            event_type = (
                "capability.undeclared"
                if phase == "started"
                else "provider.tool.failed"
            )
        else:
            event_type = f"{kind}.{'failed' if error else phase}"
        self._record_native_event(
            event_type=event_type,
            semantic=semantic,
            provider_identity=(
                _adk_identity(tool_context) or self._next_identity(kind)
            ),
            data={"error": error},
        )

    def _on_grounding_metadata(self, event: object) -> None:
        metadata = getattr(event, "grounding_metadata", None)
        if metadata is None:
            return
        entry_point = getattr(metadata, "search_entry_point", None)
        chunks = getattr(metadata, "grounding_chunks", None)
        supports = getattr(metadata, "grounding_supports", None)
        semantic = self._native_agent_semantic(getattr(event, "author", None))
        self._record_native_event(
            event_type="provider.grounding_metadata",
            semantic=semantic,
            provider_identity=_adk_identity(event)
            or self._next_identity("grounding"),
            data={
                "grounding_chunk_count": _sequence_length(chunks),
                "grounding_support_count": _sequence_length(supports),
                "search_entry_point_present": entry_point is not None,
                "rendered_content_present": bool(
                    getattr(entry_point, "rendered_content", None)
                ),
            },
        )

    def _next_identity(self, kind: str) -> str:
        self._native_sequence += 1
        return f"{kind}:{self._native_sequence}"


class _GoogleADKPluginBridge:
    def __init__(self, router: GoogleADKNormalizedTraceRouter) -> None:
        self.router = router

    def _session(self) -> GoogleADKNormalizedTraceSession | None:
        session = self.router.current_session
        return (
            session
            if isinstance(session, GoogleADKNormalizedTraceSession)
            else None
        )

    def before_run(self, invocation_context: object) -> None:
        session = self._session()
        if session is not None:
            session._on_run_start(invocation_context)

    def after_run(self, invocation_context: object) -> None:
        session = self._session()
        if session is not None:
            session._on_run_end(invocation_context, error=False)

    def run_error(self, invocation_context: object, error: Exception) -> None:
        session = self._session()
        if session is not None:
            session._on_run_end(invocation_context, error=True, exception=error)

    def before_agent(self, agent: object, callback_context: object) -> None:
        del callback_context
        session = self._session()
        if session is not None:
            session._on_agent_start(agent)

    def after_agent(self, agent: object, callback_context: object) -> None:
        del callback_context
        session = self._session()
        if session is not None:
            session._on_agent_end(agent, error=False)

    def agent_error(
        self,
        agent: object,
        callback_context: object,
        error: Exception,
    ) -> None:
        del callback_context, error
        session = self._session()
        if session is not None:
            session._on_agent_end(agent, error=True)

    def before_model(self, callback_context: object, llm_request: object) -> None:
        del llm_request
        session = self._session()
        if session is not None:
            session._on_model_start(callback_context)

    def after_model(self, callback_context: object, llm_response: object) -> None:
        session = self._session()
        if session is not None:
            session._on_model_end(callback_context, error=False, llm_response=llm_response)

    def model_error(
        self,
        callback_context: object,
        llm_request: object,
        error: Exception,
    ) -> None:
        del llm_request
        session = self._session()
        if session is not None:
            session._on_model_end(callback_context, error=True, exception=error)

    def before_tool(
        self,
        tool: object,
        tool_args: dict[str, Any],
        tool_context: object,
    ) -> None:
        del tool_args
        session = self._session()
        if session is not None:
            session._on_tool(
                tool,
                tool_context,
                phase="started",
                error=False,
            )

    def after_tool(
        self,
        tool: object,
        tool_args: dict[str, Any],
        tool_context: object,
        result: dict[str, Any],
    ) -> None:
        del tool_args, result
        session = self._session()
        if session is not None:
            session._on_tool(
                tool,
                tool_context,
                phase="completed",
                error=False,
            )

    def tool_error(
        self,
        tool: object,
        tool_args: dict[str, Any],
        tool_context: object,
        error: Exception,
    ) -> None:
        del tool_args, error
        session = self._session()
        if session is not None:
            session._on_tool(
                tool,
                tool_context,
                phase="failed",
                error=True,
            )

    def on_event(self, invocation_context: object, event: object) -> None:
        del invocation_context
        session = self._session()
        if session is not None:
            session._on_grounding_metadata(event)


def _adk_identity(value: object) -> str | None:
    for attribute in (
        "invocation_id",
        "function_call_id",
        "request_id",
        "response_id",
        "interaction_id",
        "live_session_id",
        "id",
    ):
        candidate = getattr(value, attribute, None)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


def _context_semantic(
    session: GoogleADKNormalizedTraceSession,
    callback_context: object,
) -> TraceSemanticRefs:
    agent_name = getattr(callback_context, "agent_name", None)
    return session._native_agent_semantic(agent_name)


def _sequence_length(value: object) -> int:
    return len(value) if isinstance(value, Sequence) else 0


def _adk_bool(value: object | None, name: str) -> bool:
    candidate = getattr(value, name, None)
    return candidate is True


def _adk_refusal(value: object | None) -> bool:
    if _adk_bool(value, "refusal") or _adk_bool(value, "refused"):
        return True
    finish_reason = getattr(value, "finish_reason", None)
    if isinstance(finish_reason, str):
        return finish_reason.strip().lower() in {
            "blocked",
            "content_filter",
            "safety",
            "safety_block",
        }
    return False


def _adk_safe_code(value: object | None) -> str | None:
    for name in ("error_code", "code"):
        candidate = getattr(value, name, None)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
            return str(candidate)
        if isinstance(candidate, str) and candidate.strip() and len(candidate) <= 128 and all(
            char.isalnum() or char in "._:-" for char in candidate
        ):
            return candidate
    return None


def _adk_status_code(value: object | None) -> int | None:
    candidate = getattr(value, "status_code", None)
    if isinstance(candidate, int) and not isinstance(candidate, bool) and 0 <= candidate <= 999:
        return candidate
    return None


def _adk_error_classification(value: object) -> tuple[str, str]:
    status = _adk_status_code(value)
    if status == 401:
        return "authentication", "inferred"
    if status == 403:
        return "authorization", "inferred"
    if status == 429:
        return "rate_limit", "inferred"
    if isinstance(status, int) and status >= 500:
        return "provider_error", "inferred"
    return "unknown", "observed"


def _adk_usage_value(value: object | None, *names: str) -> int | None:
    for name in names:
        candidate = value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
            return candidate
    return None


def _adk_usage_evidence(
    response: object | None,
    *,
    agent_id: object,
    attempt: object,
    identity: str,
) -> ProviderUsageEvidence:
    usage = getattr(response, "usage_metadata", None)
    from contract4agents.ir import SemanticId
    from contract4agents.tracing._models import TraceAttempt

    if not isinstance(agent_id, SemanticId) or not isinstance(attempt, TraceAttempt):
        raise TypeError("ADK usage evidence requires normalized agent and attempt identities")
    if usage is None:
        return ProviderUsageEvidence(
            scope="model_call",
            coverage="unavailable",
            aggregation_identity=identity,
            aggregation_basis="one Google ADK LlmResponse callback",
            provenance="google-adk.LlmResponse.usage_metadata",
            agent_id=agent_id,
            attempt_id=attempt.attempt_id,
            invocation_id=attempt.invocation_id,
        )
    input_tokens = _adk_usage_value(
        usage, "prompt_token_count", "promptTokenCount", "input_tokens", "inputTokens"
    )
    cached = _adk_usage_value(
        usage,
        "cached_content_token_count",
        "cachedContentTokenCount",
        "cached_input_tokens",
        "cachedInputTokens",
    )
    output_tokens = _adk_usage_value(
        usage, "candidates_token_count", "candidatesTokenCount", "output_tokens", "outputTokens"
    )
    reasoning = _adk_usage_value(
        usage, "thoughts_token_count", "thoughtsTokenCount", "reasoning_tokens", "reasoningTokens"
    )
    total = _adk_usage_value(
        usage, "total_token_count", "totalTokenCount", "total_tokens", "totalTokens"
    )
    if cached is not None and input_tokens is not None and cached > input_tokens:
        cached = None
    if input_tokens is not None and output_tokens is not None and total is not None:
        if reasoning is not None and reasoning > output_tokens:
            reasoning = None
        # Provider totals may include categories that are not exposed by ADK;
        # retain the provider fact as partial instead of inventing a correction.
        if total != input_tokens + output_tokens:
            total = None
            coverage = "partial"
        else:
            coverage = "complete"
    else:
        coverage = "partial"
    return ProviderUsageEvidence(
        scope="model_call",
        coverage=coverage,  # type: ignore[arg-type]
        aggregation_identity=identity,
        aggregation_basis="one Google ADK LlmResponse callback",
        provenance="google-adk.LlmResponse.usage_metadata",
        request_count=1,
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning,
        total_tokens=total,
        agent_id=agent_id,
        attempt_id=attempt.attempt_id,
        invocation_id=attempt.invocation_id,
    )


__all__ = [
    "GoogleADKNormalizedTraceRouter",
    "GoogleADKNormalizedTraceSession",
]
