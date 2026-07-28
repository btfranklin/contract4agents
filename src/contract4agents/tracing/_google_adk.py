"""Google ADK plugin callbacks normalized into contract-bound trace evidence."""

from __future__ import annotations

from collections.abc import Sequence
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
from contract4agents.tracing._sinks import NormalizedTraceSink

_GOOGLE_ADK_CAPTURED_CHANNELS: frozenset[TraceInstrumentationChannel] = frozenset(
    {"agent", "approval", "composition", "output", "provider_response", "tool"}
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
        """Record one ToolContext confirmation decision."""

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
        """Record the ToolContext confirmation pause surfaced to the host."""

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

    def _on_run_end(self, invocation_context: object, *, error: bool) -> None:
        del invocation_context
        if not self._provider_trace_context.get():
            return
        identity = self._next_identity("run")
        if error:
            self._run_failed = True
            self._record_native_event(
                event_type="agent.failed",
                semantic=TraceSemanticRefs(
                    agent_id=self._maybe_current_agent_id()
                ),
                provider_identity=identity,
                data={"error": True},
            )
            self._mark_response_unverified(
                "The Google ADK run failed without terminal response evidence."
            )
        elif not self._run_failed:
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

    def _on_model_end(self, callback_context: object, *, error: bool) -> None:
        identity = self._next_identity("model")
        if error:
            self._record_native_event(
                event_type="provider.response.failed",
                semantic=_context_semantic(self, callback_context),
                provider_identity=identity,
                data={"error": True},
            )
            self._mark_response_unverified(
                "The Google ADK model callback failed without response evidence."
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
        del error
        session = self._session()
        if session is not None:
            session._on_run_end(invocation_context, error=True)

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
        del llm_response
        session = self._session()
        if session is not None:
            session._on_model_end(callback_context, error=False)

    def model_error(
        self,
        callback_context: object,
        llm_request: object,
        error: Exception,
    ) -> None:
        del llm_request, error
        session = self._session()
        if session is not None:
            session._on_model_end(callback_context, error=True)

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


__all__ = [
    "GoogleADKNormalizedTraceRouter",
    "GoogleADKNormalizedTraceSession",
]
