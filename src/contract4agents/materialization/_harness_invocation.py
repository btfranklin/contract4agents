"""Contract-bound Harness calls with explicit application continuation."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import cast

from pydantic import ValidationError

from contract4agents._strict_json import json_object
from contract4agents.ir import FrozenJsonValue, FrozenMap
from contract4agents.materialization._harness_configuration import (
    HarnessAgentConfiguration,
    HarnessTool,
    canonical_json,
    content_digest,
    frozen_object,
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
        if approval is not None and approval.call_digest != pending.call_digest:
            raise ValueError("Restored approval does not identify this pending call")
        with self._state_lock:
            if pending.call_digest in self._consumed:
                raise ValueError("This pending call has already been submitted")
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
        with self._state_lock:
            self._require_pending(pending, context)
            self._pending.pop(pending.call_digest)
            self._consumed.add(pending.call_digest)
            self._approvals.pop(pending.call_digest, None)
        trace.emit(
            "composition.completed" if tool.child_agent_id else "tool.result_submitted",
            semantic=semantic,
            data={"call_digest": pending.call_digest, "denied": denied, "external_effect_verified": False},
            span_id=pending.call_digest,
        )
        return self._request(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "toolResult": {
                                "toolUseId": pending.tool_use_id,
                                "content": [{"text": canonical_json(serialized)}],
                                "status": "error" if denied else "success",
                            }
                        }
                    ],
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
        try:
            client = self.client or cast(
                HarnessRuntimeClient,
                aws_client(
                    "bedrock-agentcore",
                    region=self.deployment.harness_arn.split(":")[3],
                ),
            )
            settings = HarnessInvocationSettings.from_configuration(self.configuration.request)
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
            events = response["stream"]
            try:
                metadata = json_object("Harness response metadata", response.get("ResponseMetadata", {}))
                observed_request_id = metadata.get("RequestId")
                if observed_request_id is not None and not isinstance(observed_request_id, str):
                    raise TypeError("Harness request ID must be a string")
                request_id = observed_request_id
                if not isinstance(events, Iterable) or isinstance(events, str | bytes | Mapping):
                    raise TypeError("Harness response stream must be an event iterable")
                stream = HarnessStream.read(events)
            finally:
                close = getattr(events, "close", None)
                if callable(close):
                    close()
        except ImportError:
            raise
        except Exception as exc:
            # Provider exceptions can contain customer content and credentials.
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
            result = self._interrupt(stream, context, source_input, trace, request_id)
        elif stream.stop_reason == "end_turn":
            try:
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

    def _interrupt(
        self,
        stream: HarnessStream,
        context: HarnessRunContext,
        source_input: FrozenMap[str, FrozenJsonValue],
        trace: HarnessTraceEmitter,
        request_id: str | None,
    ) -> HarnessResult:
        tool = self.configuration.tools.get(stream.tool_name or "")
        if tool is None:
            return HarnessFailed("ungranted_tool", request_id, stream.stop_reason, True)
        try:
            self._tool_input(tool, stream.arguments, source_input)
        except (ValueError, ValidationError):
            return HarnessFailed("invalid_tool_input", request_id, stream.stop_reason, True)
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
        with self._state_lock:
            self._pending[pending.call_digest] = pending
        trace.emit(
            "composition.started" if tool.child_agent_id else "tool.requested",
            semantic=self._tool_semantic(tool),
            data={"call_digest": pending.call_digest, "arguments_digest": content_digest(pending.arguments)},
            request_id=request_id,
            event_id=event_id,
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
