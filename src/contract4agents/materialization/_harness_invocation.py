"""Contract-bound Harness calls with explicit application continuation."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Literal, cast

from pydantic import ValidationError

from contract4agents._strict_json import json_array, json_object, json_string
from contract4agents.ir import FrozenJsonValue, FrozenMap
from contract4agents.materialization._harness_batch import HarnessBatchCall, HarnessBatchResult, HarnessPendingBatch
from contract4agents.materialization._harness_configuration import (
    HarnessAgentConfiguration,
    HarnessTool,
    canonical_json,
    content_digest,
    frozen_object,
    thaw,
)
from contract4agents.materialization._harness_deployment import (
    HarnessDeployment,
    HarnessInvocationSettings,
    HarnessRuntimeClient,
    aws_client,
)
from contract4agents.materialization._harness_models import (
    HarnessApproval,
    HarnessCompleted,
    HarnessFailed,
    HarnessInterrupted,
    HarnessPendingCall,
    HarnessResult,
    HarnessRunContext,
)
from contract4agents.materialization._harness_stream import HarnessStream, decode_json
from contract4agents.materialization._types import normalize_structural_value
from contract4agents.tracing import (
    NormalizedTraceSink,
    ProviderOutcomeEvidence,
    ProviderUsageEvidence,
    TraceRunContext,
    TraceSemanticRefs,
)
from contract4agents.tracing._agentcore_harness import HarnessTraceEmitter
from contract4agents.tracing._provider_evidence import ProviderOutcome, ProviderOutcomeCategory, ProviderOutcomePhase

LOGGER = logging.getLogger(__name__)
_FailurePhase = Literal["setup", "request", "response_validation", "stream_read", "stream_close"]
_FAILURE_CLASSES = frozenset({
    "EventStreamError", "ClientError", "TimeoutError", "ReadTimeoutError", "ConnectTimeoutError",
    "ConnectionError", "EndpointConnectionError", "ConnectionClosedError", "ProxyConnectionError", "SSLError",
    "HTTPClientError", "ResponseStreamingError", "IncompleteReadError", "ProtocolError", "ResponseParserError",
    "ParserError", "DuplicateHeader", "InvalidHeadersLength", "InvalidPayloadLength", "ChecksumMismatch",
    "NoInitialResponseError", "NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError",
    "ValueError", "TypeError", "KeyError",
})
_AWS_ERROR_CODES = frozenset({
    "ServiceQuotaExceededException", "AccessDeniedException", "RuntimeClientError", "ValidationException",
    "ResourceNotFoundException", "ThrottlingException", "InternalServerException",
    "internalServerException", "validationException", "runtimeClientError",
    "AccessDenied", "ExpiredTokenException", "UnrecognizedClientException", "InvalidSignatureException",
    "TooManyRequestsException",
})


def _diagnostic_uuid(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None
    return value if str(parsed) == value else None


def _log_request_failure(
    error: Exception, phase: _FailurePhase, context: HarnessRunContext, request_id: str | None,
) -> None:
    response = getattr(error, "response", None)
    details = response.get("Error") if isinstance(response, Mapping) else None
    code = details.get("Code") if isinstance(details, Mapping) else None
    error_class = type(error).__name__
    LOGGER.error("%s", json.dumps({
        "event": "agentcore_harness_request_failure",
        "phase": phase,
        "exception_class": error_class if error_class in _FAILURE_CLASSES else "other",
        "aws_error_code": code if isinstance(code, str) and code in _AWS_ERROR_CODES else
        None if code is None else "other",
        "request_id": _diagnostic_uuid(request_id),
        "run_id": _diagnostic_uuid(context.run_id),
        "invocation_id": _diagnostic_uuid(context.attempt.invocation_id),
    }, ensure_ascii=True, separators=(",", ":")))


@dataclass(frozen=True)
class HarnessAgent:
    """A typed client handle for one explicitly verified deployed agent."""

    configuration: HarnessAgentConfiguration
    deployment: HarnessDeployment
    client: HarnessRuntimeClient | None = field(default=None, repr=False)
    trace_sink: NormalizedTraceSink | None = field(default=None, repr=False)
    _pending: dict[str, HarnessPendingCall] = field(default_factory=dict, init=False, repr=False, compare=False)
    _consumed: set[str] = field(default_factory=set, init=False, repr=False, compare=False)
    _approvals: dict[str, HarnessApproval] = field(default_factory=dict, init=False, repr=False, compare=False)
    _sessions: set[str] = field(default_factory=set, init=False, repr=False, compare=False)
    _state_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False, compare=False)

    def invoke(
        self,
        inputs: Mapping[str, object],
        *,
        context: HarnessRunContext,
        overrides: Mapping[str, object] | None = None,
    ) -> HarnessResult:
        """Validate input and start one invocation without retries or tool execution."""

        self._check_overrides(overrides)
        source_input = self._input(inputs)
        with self._state_lock:
            if context.session_id in self._sessions:
                raise ValueError("Use a fresh session for a new call; use resume for a pending call")
            self._sessions.add(context.session_id)
        return self._request(
            [{"role": "user", "content": [{"text": canonical_json(source_input)}]}],
            context=context,
            source_input=source_input,
        )

    def restore_pending(
        self,
        pending: HarnessPendingCall,
        *,
        context: HarnessRunContext,
        approval: HarnessApproval | None = None,
    ) -> None:
        """Restore application-authenticated recovery state without an AWS call.

        The application must authenticate this persisted record and prevent
        replay across processes. Recovery state contains customer arguments.
        """

        self._validate_pending_identity(pending, context)
        self._tool_input(self.configuration.tools[pending.tool_name], pending.arguments, pending.source_input)
        batch = self._validate_pending_batch(pending, context)
        if approval is not None and approval.call_digest != pending.call_digest:
            raise ValueError("Restored approval does not identify this pending call")
        with self._state_lock:
            if pending.call_digest in self._consumed:
                raise ValueError("This pending call has already been submitted")
            if any(
                other.context.session_id == context.session_id and other != pending
                for other in self._pending.values()
            ):
                raise ValueError("Another pending call already owns this session")
            if batch is not None:
                self._consumed.update(
                    batch.prior_pending(pending, index).call_digest for index in range(len(batch.results))
                )
            self._pending[pending.call_digest] = pending
            self._sessions.add(context.session_id)
            if approval is not None:
                self._approvals[pending.call_digest] = approval

    def record_approval(
        self,
        pending: HarnessPendingCall,
        approval: HarnessApproval,
        *,
        context: HarnessRunContext,
    ) -> None:
        """Record an exact application decision before the host executes a tool."""

        tool = self._require_pending(pending, context)
        if approval.call_digest != pending.call_digest:
            raise ValueError("Approval does not identify this exact pending call")
        with self._state_lock:
            prior = self._approvals.get(pending.call_digest)
            if prior is not None:
                if prior != approval:
                    raise ValueError("This call already has a different recorded approval")
                return
            self._trace(context, parent_event_id=pending.event_id).emit(
                "approval.completed",
                semantic=self._tool_semantic(tool),
                data={"approved": approval.approved, "call_digest": pending.call_digest},
                evidence_refs=(approval.evidence_ref,),
                span_id=pending.call_digest,
            )
            self._approvals[pending.call_digest] = approval

    def tool_arguments(self, pending: HarnessPendingCall, *, context: HarnessRunContext) -> dict[str, object]:
        """Return validated Python arguments for application-owned tool execution."""

        tool = self._require_pending(pending, context)
        return self._tool_input(tool, pending.arguments, pending.source_input)

    def resume(
        self,
        pending: HarnessPendingCall,
        value: object = None,
        *,
        context: HarnessRunContext,
        approval: HarnessApproval | None = None,
        overrides: Mapping[str, object] | None = None,
    ) -> HarnessResult:
        """Validate an exact approval and result, then resume one pending call."""

        self._check_overrides(overrides)
        tool = self._require_pending(pending, context)
        if approval is not None and approval.call_digest != pending.call_digest:
            raise ValueError("Approval does not identify this exact pending call")
        if tool.approval_required and approval is None:
            raise ValueError("This tool requires an explicit application approval")
        if approval is not None and self._approvals.get(pending.call_digest) != approval:
            raise ValueError("Record the application approval before tool execution and continuation")
        trace = self._trace(context, parent_event_id=pending.event_id)
        semantic = self._tool_semantic(tool)
        denied = approval is not None and not approval.approved
        if denied:
            serialized: object = {"error": "application_approval_denied"}
        else:
            # Domain validation and proof of the external effect remain host duties.
            validated = tool.output_adapter.validate_python(normalize_structural_value(value), strict=True)
            serialized = tool.output_adapter.dump_python(validated, mode="json")
        response = frozen_object({
            "toolUseId": pending.tool_use_id,
            "content": [{"text": canonical_json(serialized)}],
            "status": "error" if denied else "success",
        })
        batch = HarnessPendingBatch.from_dict(pending.batch) if pending.batch is not None else None
        results = (() if batch is None else batch.results) + (HarnessBatchResult(response, approval),)
        buffered = batch is not None and len(results) < len(batch.calls)
        following = (
            HarnessPendingBatch(batch.calls, results).pending(pending) if buffered and batch is not None else None
        )
        with self._state_lock:
            self._require_pending(pending, context)
            self._pending.pop(pending.call_digest)
            self._consumed.add(pending.call_digest)
            self._approvals.pop(pending.call_digest, None)
            if following is not None:
                self._pending[following.call_digest] = following
        trace.emit(
            "composition.completed" if tool.child_agent_id else "tool.result_submitted",
            semantic=semantic,
            data={
                "call_digest": pending.call_digest, "denied": denied,
                "external_effect_verified": False,
                "submission_phase": "buffered" if buffered else "dispatch_requested",
            },
            span_id=pending.call_digest,
        )
        if following is not None:
            return self._emit_pending(following, trace, request_id=None)
        return self._request(
            [
                {
                    "role": "user",
                    "content": [{"toolResult": thaw(item.response)} for item in results],
                }
            ],
            context=context,
            source_input=pending.source_input,
            parent_event_id=pending.event_id,
        )

    def invoke_delegate(
        self,
        pending: HarnessPendingCall,
        child: HarnessAgent,
        *,
        context: HarnessRunContext,
        child_context: HarnessRunContext,
    ) -> HarnessResult:
        """Invoke only the declared child in an application-supplied fresh session."""

        tool = self._require_pending(pending, context)
        if (
            tool.child_agent_id is None
            or tool.child_agent_id != child.configuration.agent_id
            or child.deployment.plan_digest != self.deployment.plan_digest
            or child.deployment.contract_digest != self.deployment.contract_digest
        ):
            raise ValueError("The handle is not this call's declared child")
        if (
            child_context.session_id == context.session_id
            or child_context.principal_id != context.principal_id
            or child_context.authorization_context != context.authorization_context
            or child_context.run_id != context.run_id
            or child_context.thread_id != context.thread_id
            or child_context.parent_event_id != pending.event_id
            or child_context.attempt.invocation_id == context.attempt.invocation_id
        ):
            raise ValueError("Delegation requires a fresh child scope linked to the pending call")
        return child.invoke(self.tool_arguments(pending, context=context), context=child_context)

    def _input(self, value: Mapping[str, object]) -> FrozenMap[str, FrozenJsonValue]:
        if not isinstance(value, Mapping):
            raise ValueError("Harness input must be an object")
        if self.configuration.input_type is None:
            if value:
                raise ValueError("This agent takes no input")
            return frozen_object({})
        validated = self.configuration.input_type.model_validate(dict(value))
        return frozen_object(validated.model_dump(mode="json"))

    def _validate_pending_identity(self, pending: HarnessPendingCall, context: HarnessRunContext) -> None:
        if (
            pending.context != context
            or pending.agent_id != str(self.configuration.agent_id)
            or pending.contract_digest != self.deployment.contract_digest
            or pending.plan_digest != self.deployment.plan_digest
            or pending.configuration_digest != self.configuration.configuration_digest
            or pending.deployment != frozen_object(self.deployment.identity)
            or pending.tool_name not in self.configuration.tools
        ):
            raise ValueError("Pending call identity does not match the handle and application context")

    def _require_pending(self, pending: HarnessPendingCall, context: HarnessRunContext) -> HarnessTool:
        self._validate_pending_identity(pending, context)
        with self._state_lock:
            if self._pending.get(pending.call_digest) != pending or pending.call_digest in self._consumed:
                raise ValueError("Pending call is unknown, changed, or already submitted")
        return self.configuration.tools[pending.tool_name]

    def _validate_pending_batch(
        self, pending: HarnessPendingCall, context: HarnessRunContext,
    ) -> HarnessPendingBatch | None:
        if pending.batch is None:
            return None
        batch = HarnessPendingBatch.from_dict(pending.batch)
        if batch.pending(pending) != pending:
            raise ValueError("Pending call does not match its batch position")
        for call in batch.calls:
            candidate = replace(
                pending, tool_name=call.tool_name, tool_use_id=call.tool_use_id,
                arguments=call.arguments, event_id=call.event_id, batch=None,
            )
            self._validate_pending_identity(candidate, context)
            self._tool_input(self.configuration.tools[call.tool_name], call.arguments, pending.source_input)
        for index, result in enumerate(batch.results):
            prior = batch.prior_pending(pending, index)
            tool = self.configuration.tools[prior.tool_name]
            approval = result.approval
            if tool.approval_required and approval is None:
                raise ValueError("Buffered result is missing its application approval")
            if approval is not None and approval.call_digest != prior.call_digest:
                raise ValueError("Buffered approval does not identify its exact pending call")
            response = json_object("Buffered tool result", thaw(result.response))
            content = json_object("Buffered tool content", json_array("Buffered content", response["content"])[0])
            text = json_string("Buffered result text", content["text"])
            value = decode_json(text)
            if response["status"] == "error":
                if approval is None or approval.approved or value != {"error": "application_approval_denied"}:
                    raise ValueError("Buffered denial does not match its application decision")
            else:
                if approval is not None and not approval.approved:
                    raise ValueError("Buffered successful result has a denied approval")
                tool.output_adapter.validate_json(text, strict=True)
        return batch

    def _tool_input(
        self,
        tool: HarnessTool,
        arguments: object,
        source_input: Mapping[str, object],
    ) -> dict[str, object]:
        if not isinstance(arguments, Mapping):
            raise ValueError("Tool arguments must be an object")
        normalized_arguments: dict[str, object] = {}
        if tool.input_type is None:
            if arguments:
                raise ValueError("This tool takes no arguments")
            result: dict[str, object] = {}
        else:
            validated = tool.input_type.model_validate_json(canonical_json(arguments))
            result = validated.model_dump(mode="python")
            normalized_arguments = validated.model_dump(mode="json")
        for key, expression in tool.input_mappings.items():
            parts = expression.split(".")
            if len(parts) < 2 or parts[0] != "input":
                raise ValueError("Unsupported delegate input mapping")
            expected: object = source_input
            for part in parts[1:]:
                if not isinstance(expected, Mapping) or part not in expected:
                    raise ValueError("Delegate source input is unavailable")
                expected = expected[part]
            actual = normalized_arguments.get(key)
            if content_digest(expected) != content_digest(actual):
                raise ValueError("Delegate arguments do not match the declared source input")
        return result

    @staticmethod
    def _check_overrides(overrides: Mapping[str, object] | None) -> None:
        if overrides:
            raise ValueError("Harness invocation overrides require a new reviewed target profile")

    def _trace(self, context: HarnessRunContext, *, parent_event_id: str | None = None) -> HarnessTraceEmitter:
        return HarnessTraceEmitter(
            context=TraceRunContext(
                context.run_id, context.thread_id, self.deployment.contract_digest, self.deployment.plan_digest
            ),
            attempt=context.attempt,
            agent_id=self.configuration.agent_id,
            deployment=self.deployment.identity,
            sink=self.trace_sink,
            parent_event_id=parent_event_id or context.parent_event_id,
        )

    def _tool_semantic(self, tool: HarnessTool) -> TraceSemanticRefs:
        return TraceSemanticRefs(
            agent_id=self.configuration.agent_id,
            grant_id=tool.grant_id,
            capability_id=tool.semantic_id if tool.grant_id else None,
            composition_id=tool.semantic_id if tool.child_agent_id else None,
        )

    def _request(
        self,
        messages: list[dict[str, object]],
        *,
        context: HarnessRunContext,
        source_input: FrozenMap[str, FrozenJsonValue],
        parent_event_id: str | None = None,
    ) -> HarnessResult:
        trace = self._trace(context, parent_event_id=parent_event_id)
        trace.emit("agent.started")
        request_id: str | None = None
        response_key = str(uuid.uuid4())
        response_received = False
        phase: _FailurePhase = "setup"
        try:
            settings = HarnessInvocationSettings.from_configuration(self.configuration.request)
            client = self.client or cast(
                HarnessRuntimeClient,
                aws_client(
                    "bedrock-agentcore",
                    region=self.deployment.harness_arn.split(":")[3],
                    read_timeout=settings.timeout_seconds,
                ),
            )
            phase = "request"
            response = client.invoke_harness(
                harnessArn=self.deployment.harness_arn,
                qualifier=self.deployment.endpoint_name,
                runtimeSessionId=context.session_id,
                runtimeUserId=context.principal_id,
                actorId=context.principal_id,
                messages=messages,
                model=settings.model,
                systemPrompt=settings.system_prompt,
                tools=settings.tools,
                allowedTools=settings.allowed_tools,
                maxIterations=settings.max_iterations,
                maxTokens=settings.max_tokens,
                timeoutSeconds=settings.timeout_seconds,
            )
            response_received = True
            phase = "response_validation"
            events = response["stream"]
            try:
                metadata = json_object("Harness response metadata", response.get("ResponseMetadata", {}))
                observed_request_id = metadata.get("RequestId")
                if observed_request_id is not None and not isinstance(observed_request_id, str):
                    raise TypeError("Harness request ID must be a string")
                request_id = observed_request_id
                if not isinstance(events, Iterable) or isinstance(events, str | bytes | Mapping):
                    raise TypeError("Harness response stream must be an event iterable")
                phase = "stream_read"
                stream = HarnessStream.read(events)
            finally:
                prior_phase = phase
                phase = "stream_close"
                close = getattr(events, "close", None)
                if callable(close):
                    close()
                phase = prior_phase
        except ImportError:
            raise
        except Exception as exc:
            # Provider exceptions can contain customer content and credentials.
            _log_request_failure(exc, phase, context, request_id)
            failed = HarnessFailed(
                _transport_reason(exc),
                request_id,
                response_received=response_received or isinstance(getattr(exc, "response", None), Mapping),
            )
            self._finish(trace, failed, context, request_id)
            return failed
        self._usage(trace, stream, context, request_id or response_key)
        if stream.failure:
            result: HarnessResult = HarnessFailed(stream.failure, request_id, stream.stop_reason, True)
        elif stream.stop_reason == "tool_use":
            if (
                self.configuration.output_mode == "tool"
                and self.configuration.result_tool_name in stream.tool_use_names
            ):
                if stream.tool_use_count != 1 or stream.tool_name != self.configuration.result_tool_name:
                    trace.emit("output.schema_failed", request_id=request_id)
                    result = HarnessFailed("invalid_output", request_id, stream.stop_reason, True)
                else:
                    result = self._complete_tool_output(stream, trace, request_id)
            else:
                result = self._interrupt(stream, context, source_input, trace, request_id)
        elif stream.stop_reason == "end_turn":
            try:
                if self.configuration.output_mode == "tool":
                    raise ValueError("Tool output mode requires the configured result tool")
                decode_json(stream.text)
                value = self.configuration.output_adapter.validate_json(stream.text, strict=True)
                result = HarnessCompleted(value, request_id)
            except (ValueError, ValidationError):
                trace.emit("output.schema_failed", request_id=request_id)
                result = HarnessFailed("invalid_output", request_id, stream.stop_reason, True)
        else:
            reason = {
                "content_filtered": "refusal",
                "timeout_exceeded": "timeout",
                "max_iterations_exceeded": "iteration_limit",
                "max_tokens": "token_limit",
                "max_output_tokens_exceeded": "token_limit",
                "hook_stopped": "hook_stopped",
            }.get(stream.stop_reason or "", "incomplete_execution")
            result = HarnessFailed(reason, request_id, stream.stop_reason, True)
        self._finish(trace, result, context, request_id)
        return result

    def _complete_tool_output(
        self, stream: HarnessStream, trace: HarnessTraceEmitter, request_id: str | None
    ) -> HarnessResult:
        try:
            if not isinstance(stream.arguments, Mapping) or set(stream.arguments) != {"value"}:
                raise ValueError("Invalid result envelope")
            value = self.configuration.output_adapter.validate_json(
                canonical_json(stream.arguments["value"]), strict=True
            )
        except (ValueError, ValidationError):
            trace.emit("output.schema_failed", request_id=request_id)
            return HarnessFailed("invalid_output", request_id, stream.stop_reason, True)
        return HarnessCompleted(value, request_id)

    def _interrupt(
        self,
        stream: HarnessStream,
        context: HarnessRunContext,
        source_input: FrozenMap[str, FrozenJsonValue],
        trace: HarnessTraceEmitter,
        request_id: str | None,
    ) -> HarnessResult:
        for call in stream.tool_calls:
            tool = self.configuration.tools.get(call.tool_name)
            if tool is None:
                return HarnessFailed("ungranted_tool", request_id, stream.stop_reason, True)
            try:
                self._tool_input(tool, call.arguments, source_input)
            except (ValueError, ValidationError):
                return HarnessFailed("invalid_tool_input", request_id, stream.stop_reason, True)
        tool = self.configuration.tools[stream.tool_name or ""]
        event_id = f"agentcore_harness:{uuid.uuid4()}"
        pending = HarnessPendingCall(
            str(self.configuration.agent_id),
            tool.name,
            stream.tool_use_id or "",
            frozen_object(stream.arguments),
            source_input,
            self.deployment.contract_digest,
            self.deployment.plan_digest,
            self.configuration.configuration_digest,
            frozen_object(self.deployment.identity),
            context,
            event_id,
        )
        if len(stream.tool_calls) > 1:
            batch = HarnessPendingBatch(tuple(
                HarnessBatchCall(call.tool_name, call.tool_use_id, call.arguments, f"agentcore_harness:{uuid.uuid4()}")
                for call in stream.tool_calls
            ))
            pending = batch.pending(pending)
        return self._register_pending(pending, trace, request_id)

    def _register_pending(
        self, pending: HarnessPendingCall, trace: HarnessTraceEmitter, request_id: str | None,
    ) -> HarnessInterrupted:
        with self._state_lock:
            self._pending[pending.call_digest] = pending
        return self._emit_pending(pending, trace, request_id)

    def _emit_pending(
        self, pending: HarnessPendingCall, trace: HarnessTraceEmitter, request_id: str | None,
    ) -> HarnessInterrupted:
        tool = self.configuration.tools[pending.tool_name]
        trace.emit(
            "composition.started" if tool.child_agent_id else "tool.requested",
            semantic=self._tool_semantic(tool),
            data={"call_digest": pending.call_digest, "arguments_digest": content_digest(pending.arguments)},
            request_id=request_id,
            event_id=pending.event_id,
            span_id=pending.call_digest,
        )
        if tool.approval_required:
            trace.emit(
                "approval.requested",
                semantic=self._tool_semantic(tool),
                data={"call_digest": pending.call_digest},
                request_id=request_id,
                span_id=pending.call_digest,
            )
        return HarnessInterrupted(pending, request_id)

    def _finish(
        self,
        trace: HarnessTraceEmitter,
        result: HarnessResult,
        context: HarnessRunContext,
        request_id: str | None,
    ) -> None:
        if isinstance(result, HarnessCompleted):
            trace.emit("output.accepted", data={"validation_phase": "contract_structure"}, request_id=request_id)
        phase: ProviderOutcomePhase = "response"
        outcome: ProviderOutcome = "unknown"
        category: ProviderOutcomeCategory = "unknown"
        response_received = True
        terminal_event = "agent.interrupted"
        if isinstance(result, HarnessCompleted):
            phase, outcome, category = "structured_output", "succeeded", "structured_output"
            terminal_event = "agent.completed"
        elif isinstance(result, HarnessFailed):
            outcome = "refused" if result.reason == "refusal" else "failed"
            response_received = result.response_received
            if not response_received:
                phase = "transport"
            categories: dict[str, ProviderOutcomeCategory] = {
                "refusal": "refusal",
                "invalid_output": "structured_output",
                "timeout": "provider_timeout",
                "transport_failure": "transport",
                "authentication_failure": "authentication",
                "authorization_failure": "authorization",
                "rate_limited": "rate_limit",
                "provider_error": "provider_error",
            }
            category = categories.get(result.reason, "unknown")
            terminal_event = "agent.failed"
        outcome_evidence = ProviderOutcomeEvidence(
            agent_id=self.configuration.agent_id,
            attempt_id=context.attempt.attempt_id,
            invocation_id=context.attempt.invocation_id,
            phase=phase,
            outcome=outcome,
            category=category,
            state="observed",
            classifier_provenance="agentcore-harness-client-boundary",
            request_id=request_id,
            response_received=response_received,
        )
        trace.emit("provider.outcome.reported", data={"evidence": outcome_evidence.to_dict()}, request_id=request_id)
        trace.emit(
            terminal_event,
            data={"status": result.status},
            request_id=request_id,
        )

    def _usage(
        self,
        trace: HarnessTraceEmitter,
        stream: HarnessStream,
        context: HarnessRunContext,
        response_key: str,
    ) -> None:
        counts: dict[str, int] = {}
        if len(stream.usage) == 1:
            for source, target in (
                ("inputTokens", "input_tokens"),
                ("outputTokens", "output_tokens"),
                ("totalTokens", "total_tokens"),
            ):
                value = stream.usage[0].get(source)
                if type(value) is int and value >= 0:
                    counts[target] = value
        if (
            all(key in counts for key in ("input_tokens", "output_tokens", "total_tokens"))
            and counts["total_tokens"] != counts["input_tokens"] + counts["output_tokens"]
        ):
            counts = {}
        evidence = ProviderUsageEvidence(
            scope="response",
            coverage="partial" if counts else "unavailable" if not stream.usage else "unverified",
            aggregation_identity=content_digest({"response": response_key, "attempt": context.attempt.to_dict()}),
            aggregation_basis="one_harness_response_metadata_not_model_request_count",
            provenance="agentcore-harness-stream-metadata",
            agent_id=self.configuration.agent_id,
            attempt_id=context.attempt.attempt_id,
            invocation_id=context.attempt.invocation_id,
            **counts,
        )
        trace.emit("provider.usage.reported", data={"evidence": evidence.to_dict()})


def _transport_reason(error: Exception) -> str:
    if isinstance(error, TimeoutError) or type(error).__name__ in {"ReadTimeoutError", "ConnectTimeoutError"}:
        return "timeout"
    response = getattr(error, "response", None)
    code = response.get("Error", {}).get("Code") if isinstance(response, Mapping) else None
    if code in {"AccessDeniedException", "AccessDenied"}:
        return "authorization_failure"
    if code in {"ExpiredTokenException", "UnrecognizedClientException", "InvalidSignatureException"} or type(
        error
    ).__name__ in {"NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError"}:
        return "authentication_failure"
    if code in {"ThrottlingException", "TooManyRequestsException"}:
        return "rate_limited"
    return "transport_failure"
