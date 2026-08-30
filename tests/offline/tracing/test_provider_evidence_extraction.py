from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from contract4agents import compile_project, materialize
from contract4agents.ir import (
    semantic_id,
)
from contract4agents.tracing import (
    OpenAINormalizedTraceRouter,
    ProviderOutcomeEvidence,
    ProviderUsageEvidence,
    TraceAttempt,
)
from contract4agents.tracing import _google_adk as google_adk_tracing
from contract4agents.tracing import _openai as openai_tracing
from contract4agents.tracing import _strands as strands_tracing

ROOT = Path(__file__).resolve().parents[3]


def test_session_provider_reports_bind_attempt_identity_and_close_channels() -> None:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    system = materialize(project, "openai", "test")
    router = OpenAINormalizedTraceRouter()
    session = router.open_session(artifacts.ir, system.plan, run_id="provider-evidence")
    attempt = TraceAttempt("commander:1", "commander-attempt-1", 1)
    agent_id = semantic_id("agent", "IncidentCommander")
    outcome = ProviderOutcomeEvidence(
        agent_id=agent_id,
        attempt_id=attempt.attempt_id,
        phase="response",
        outcome="succeeded",
        category="transport",
        state="observed",
        classifier_provenance="fixture",
        response_id="response-1",
    )
    usage = ProviderUsageEvidence(
        scope="attempt",
        coverage="complete",
        aggregation_identity="attempt-1",
        aggregation_basis="fixture",
        provenance="fixture",
        request_count=1,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        agent_id=agent_id,
        attempt_id=attempt.attempt_id,
    )
    with session:
        with session.bind_attempt(attempt, agent="IncidentCommander"):
            session.record_provider_outcome(outcome, provider_identity="response-1")
            session.report_provider_outcome(outcome, provider_identity="response-1")
            session.record_provider_usage(usage, provider_identity="response-1")
            session.report_provider_usage(usage, provider_identity="response-1")
            with pytest.raises(ValueError, match="attempt_id"):
                session.record_provider_outcome(
                    replace(outcome, attempt_id="other-attempt"), provider_identity="response-1"
                )
            with pytest.raises(ValueError, match="agent"):
                session.record_provider_usage(
                    replace(usage, agent_id=semantic_id("agent", "Missing")), provider_identity="response-1"
                )
        unbound = TraceAttempt("commander:2", "commander-attempt-2", 1)
        with pytest.raises(ValueError, match="requires agent_id"):
            session.record_provider_usage(
                replace(usage, agent_id=None, attempt_id=None), attempt=unbound, provider_identity="response-2"
            )
        session.record_provider_usage(
            replace(usage, aggregation_identity="unbound", attempt_id=unbound.attempt_id),
            attempt=unbound,
            provider_identity="response-2",
        )
    assert {event.event_type for event in session.normalized_trace().events} >= {
        "provider.outcome.reported",
        "provider.usage.reported",
    }
    exception_session = router.open_session(artifacts.ir, system.plan, run_id="provider-exception")
    exception_attempt = TraceAttempt("commander:exception", "commander-exception-1", 1)
    with exception_session:
        with exception_session.bind_attempt(exception_attempt, agent="IncidentCommander"):
            assert (
                exception_session.normalize_exception_responses(
                    RuntimeError("secret exception text"),
                    agent="IncidentCommander",
                    attempt=exception_attempt,
                )
                == ()
            )
    assert all(
        "secret exception text" not in json.dumps(event.to_dict())
        for event in exception_session.normalized_trace().events
    )


def test_provider_extraction_helpers_use_structured_fields_only() -> None:
    attempt = TraceAttempt("research:1", "attempt-1", 1)
    agent_id = semantic_id("agent", "Researcher")
    complete_usage = SimpleNamespace(
        requests=2,
        input_tokens=10,
        input_tokens_details=SimpleNamespace(cached_tokens=2),
        output_tokens=5,
        output_tokens_details=SimpleNamespace(reasoning_tokens=1),
        total_tokens=15,
    )
    assert (
        openai_tracing._openai_usage_evidence(
            SimpleNamespace(context_wrapper=SimpleNamespace(usage=complete_usage)),
            agent_id=agent_id,
            attempt=attempt,
        ).coverage
        == "complete"
    )
    assert (
        openai_tracing._openai_usage_evidence(
            SimpleNamespace(context_wrapper=SimpleNamespace(usage=SimpleNamespace(input_tokens=10))),
            agent_id=agent_id,
            attempt=attempt,
        ).coverage
        == "partial"
    )
    assert (
        openai_tracing._openai_usage_evidence(
            SimpleNamespace(context_wrapper=SimpleNamespace(usage=None)), agent_id=agent_id, attempt=attempt
        ).coverage
        == "unavailable"
    )
    inconsistent = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=16)
    assert (
        openai_tracing._openai_usage_evidence(
            SimpleNamespace(context_wrapper=SimpleNamespace(usage=inconsistent)), agent_id=agent_id, attempt=attempt
        ).coverage
        == "partial"
    )
    assert (
        openai_tracing._openai_usage_evidence(
            SimpleNamespace(
                context_wrapper=SimpleNamespace(
                    usage=SimpleNamespace(
                        input_tokens=10,
                        output_tokens=5,
                        total_tokens=15,
                        input_tokens_details=SimpleNamespace(cached_tokens=11),
                    )
                )
            ),
            agent_id=agent_id,
            attempt=attempt,
        ).cached_input_tokens
        is None
    )
    assert (
        openai_tracing._openai_usage_evidence(
            SimpleNamespace(context_wrapper=SimpleNamespace(usage=SimpleNamespace(requests=0))),
            agent_id=agent_id,
            attempt=attempt,
        ).coverage
        == "partial"
    )
    assert openai_tracing._classify_openai_exception(SimpleNamespace(status_code=401))[1] == "authentication"
    assert openai_tracing._classify_openai_exception(SimpleNamespace(status_code=403))[1] == "authorization"
    assert openai_tracing._classify_openai_exception(SimpleNamespace(status_code=429))[1] == "rate_limit"
    assert openai_tracing._classify_openai_exception(SimpleNamespace(status_code=500))[1] == "provider_error"
    assert openai_tracing._classify_openai_exception(asyncio.CancelledError())[3] == "unverified"
    from agents.exceptions import MCPToolCancellationError, ModelRefusalError, ModelTimeoutError

    assert openai_tracing._classify_openai_exception(ModelTimeoutError(1))[0] == "failed"
    assert openai_tracing._classify_openai_exception(ModelRefusalError("redacted"))[0] == "refused"
    assert openai_tracing._classify_openai_exception(MCPToolCancellationError("redacted"))[0] == "cancelled"
    assert openai_tracing._safe_code_attr(SimpleNamespace(code="safe-code"), "code") == "safe-code"
    assert openai_tracing._safe_code_attr(SimpleNamespace(code="unsafe code"), "code") is None
    assert openai_tracing._safe_code_attr(SimpleNamespace(code="x" * 129), "code") is None
    assert openai_tracing._safe_float_attr(SimpleNamespace(value=1), "value") == 1.0
    assert openai_tracing._safe_float_attr(SimpleNamespace(value=float("nan")), "value") is None
    assert openai_tracing._safe_float_attr(SimpleNamespace(value=True), "value") is None


def test_google_and_strands_usage_helpers_accept_public_shapes_and_reject_content() -> None:
    attempt = TraceAttempt("research:1", "attempt-1", 1)
    agent_id = semantic_id("agent", "Researcher")
    assert google_adk_tracing._adk_identity(SimpleNamespace(response_id="response-1")) == "response-1"
    assert google_adk_tracing._adk_refusal(SimpleNamespace(finish_reason="SAFETY"))
    assert google_adk_tracing._adk_refusal(SimpleNamespace(refused=True))
    assert google_adk_tracing._adk_safe_code(SimpleNamespace(error_code=429)) == "429"
    assert google_adk_tracing._adk_safe_code(SimpleNamespace(code="bad code")) is None
    assert google_adk_tracing._adk_status_code(SimpleNamespace(status_code=403)) == 403
    assert google_adk_tracing._adk_error_classification(SimpleNamespace(status_code=401)) == (
        "authentication",
        "inferred",
    )
    assert google_adk_tracing._adk_error_classification(SimpleNamespace(status_code=403)) == (
        "authorization",
        "inferred",
    )
    assert google_adk_tracing._adk_error_classification(SimpleNamespace(status_code=429)) == (
        "rate_limit",
        "inferred",
    )
    assert google_adk_tracing._adk_error_classification(SimpleNamespace(status_code=503)) == (
        "provider_error",
        "inferred",
    )
    assert google_adk_tracing._adk_error_classification(SimpleNamespace()) == ("unknown", "observed")
    adk_complete = SimpleNamespace(
        usage_metadata={
            "prompt_token_count": 10,
            "cached_content_token_count": 2,
            "candidates_token_count": 5,
            "thoughts_token_count": 1,
            "total_token_count": 15,
        }
    )
    assert (
        google_adk_tracing._adk_usage_evidence(
            adk_complete,
            agent_id=agent_id,
            attempt=attempt,
            identity="response-1",
        ).coverage
        == "complete"
    )
    assert (
        google_adk_tracing._adk_usage_evidence(
            SimpleNamespace(),
            agent_id=agent_id,
            attempt=attempt,
            identity="response-2",
        ).coverage
        == "unavailable"
    )
    adk_partial = SimpleNamespace(
        usage_metadata={"prompt_token_count": 10, "candidates_token_count": 5, "total_token_count": 16}
    )
    assert (
        google_adk_tracing._adk_usage_evidence(
            adk_partial,
            agent_id=agent_id,
            attempt=attempt,
            identity="response-5",
        ).coverage
        == "partial"
    )
    adk_invalid_categories = SimpleNamespace(
        usage_metadata={
            "prompt_token_count": 10,
            "cached_content_token_count": 11,
            "candidates_token_count": 5,
            "thoughts_token_count": 6,
            "total_token_count": 15,
        }
    )
    adk_invalid_result = google_adk_tracing._adk_usage_evidence(
        adk_invalid_categories,
        agent_id=agent_id,
        attempt=attempt,
        identity="response-6",
    )
    assert adk_invalid_result.coverage == "complete"
    assert adk_invalid_result.cached_input_tokens is None
    assert adk_invalid_result.reasoning_tokens is None
    assert google_adk_tracing._adk_usage_value(SimpleNamespace(input_tokens=3), "missing", "input_tokens") == 3
    strands_complete = SimpleNamespace(
        metrics=SimpleNamespace(
            accumulated_usage={
                "inputTokens": 10,
                "outputTokens": 5,
                "totalTokens": 15,
                "cacheReadInputTokens": 2,
            }
        )
    )
    assert (
        strands_tracing._strands_usage_evidence(
            strands_complete,
            agent_id=agent_id,
            attempt=attempt,
            identity="response-3",
        ).coverage
        == "complete"
    )
    assert (
        strands_tracing._strands_usage_evidence(
            SimpleNamespace(),
            agent_id=agent_id,
            attempt=attempt,
            identity="response-4",
        ).coverage
        == "unavailable"
    )
    strands_partial = SimpleNamespace(metrics=SimpleNamespace(accumulated_usage={"inputTokens": 10}))
    assert (
        strands_tracing._strands_usage_evidence(
            strands_partial,
            agent_id=agent_id,
            attempt=attempt,
            identity="response-5",
        ).coverage
        == "partial"
    )
    strands_mismatch = SimpleNamespace(
        metrics=SimpleNamespace(accumulated_usage={"inputTokens": 10, "outputTokens": 5, "totalTokens": 16})
    )
    assert (
        strands_tracing._strands_usage_evidence(
            strands_mismatch,
            agent_id=agent_id,
            attempt=attempt,
            identity="response-6",
        ).coverage
        == "partial"
    )
    strands_cached = SimpleNamespace(
        metrics=SimpleNamespace(
            accumulated_usage={
                "inputTokens": 10,
                "outputTokens": 5,
                "totalTokens": 15,
                "cacheReadInputTokens": 11,
            }
        )
    )
    assert (
        strands_tracing._strands_usage_evidence(
            strands_cached,
            agent_id=agent_id,
            attempt=attempt,
            identity="response-7",
        ).cached_input_tokens
        is None
    )
    assert strands_tracing._strands_usage_value({"inputTokens": True}, "inputTokens") is None
    assert [
        strands_tracing._strands_stop_outcome(value)[0]
        for value in ("cancelled", "content_filtered", "interrupt", "stop")
    ] == ["cancelled", "refused", "unknown", "succeeded"]
    assert strands_tracing._provider_identity({"request_id": "request-1"}) == "request-1"
    assert strands_tracing._tool_use_identity(SimpleNamespace(tool_use={"toolUseId": "tool-1"})) == "tool-1"
    assert strands_tracing._tool_name(SimpleNamespace(tool_use={"name": "search"})) == "search"
    assert strands_tracing._is_structured_output_tool(SimpleNamespace(tool_type="structured_output"))
