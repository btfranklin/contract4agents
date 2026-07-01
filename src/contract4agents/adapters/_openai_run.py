"""OpenAI run helpers with Contract4Agents assertion evaluation."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Mapping
from typing import Any

from contract4agents.adapters._openai_names import contract_tool_name
from contract4agents.adapters._openai_trace import OpenAITraceHooks, serializable
from contract4agents.adapters._openai_types import (
    ApprovalCallback,
    OpenAIAdapterResult,
    OpenAIAdapterUnavailable,
    OpenAIAgentFactoryError,
    OpenAIApprovalRequest,
    OpenAIContractRunResult,
)
from contract4agents.assertions import RunEvaluationResult, evaluate_run_assertions
from contract4agents.compiler import CompilerArtifacts
from contract4agents.runtime import RuntimeContext, TraceRecorder


async def run_openai_agent(
    agent: Any,
    user_input: str,
    *,
    context: Any | None = None,
    max_turns: int | None = 10,
    hooks: Any | None = None,
) -> OpenAIAdapterResult:
    try:
        from agents import Runner
    except Exception as exc:  # noqa: BLE001 - optional adapter import boundary.
        raise OpenAIAdapterUnavailable("openai-agents is not installed") from exc
    result = await Runner.run(agent, user_input, context=context, max_turns=max_turns, hooks=hooks)
    last_agent = getattr(getattr(result, "last_agent", None), "name", None)
    return OpenAIAdapterResult(getattr(result, "final_output", None), last_agent, result)


async def run_openai_agent_with_contract(
    agent: Any,
    user_input: str,
    *,
    contract: CompilerArtifacts,
    agent_name: str,
    trace: TraceRecorder | None = None,
    runtime_context: RuntimeContext | None = None,
    context: Any | None = None,
    hidden_truth: Mapping[str, Any] | None = None,
    approval_callback: ApprovalCallback | None = None,
    max_turns: int | None = 10,
    hooks: Any | None = None,
) -> OpenAIContractRunResult:
    """Run one OpenAI SDK agent and evaluate its Contract4Agents assertions."""
    try:
        from agents import Runner
    except Exception as exc:  # noqa: BLE001 - optional adapter import boundary.
        raise OpenAIAdapterUnavailable("openai-agents is not installed") from exc
    run_trace = trace or TraceRecorder()
    run_hooks = hooks or OpenAITraceHooks(run_trace)
    prompt = _input_with_rendered_context(user_input, runtime_context)
    runner_context = context if context is not None else runtime_context
    result = await Runner.run(agent, prompt, context=runner_context, max_turns=max_turns, hooks=run_hooks)
    result, approvals = await _resolve_approval_interruptions(
        Runner,
        agent,
        result,
        runner_context,
        max_turns,
        run_hooks,
        run_trace,
        approval_callback,
    )
    final_output = serializable(getattr(result, "final_output", None))
    assertion_result = evaluate_run_assertions(
        contract=contract,
        trace=run_trace,
        outputs={agent_name: final_output},
        target_agents=[agent_name],
        context=runtime_context.values if runtime_context is not None else None,
        hidden_truth=hidden_truth,
        run_id=run_trace.run_id,
    )
    _record_assertion_events(run_trace, assertion_result)
    last_agent = getattr(getattr(result, "last_agent", None), "name", None)
    return OpenAIContractRunResult(
        OpenAIAdapterResult(final_output, last_agent, result),
        assertion_result,
        run_trace,
        approvals,
    )


def _input_with_rendered_context(user_input: str, runtime_context: RuntimeContext | None) -> str:
    if runtime_context is None:
        return user_input
    rendered = runtime_context.rendered_context()
    if not rendered:
        return user_input
    return f"{user_input}\n\nResolved context:\n{rendered}"


async def _resolve_approval_interruptions(
    runner: Any,
    agent: Any,
    result: Any,
    context: Any,
    max_turns: int | None,
    hooks: Any,
    trace: TraceRecorder,
    approval_callback: ApprovalCallback | None,
) -> tuple[Any, list[OpenAIApprovalRequest]]:
    approvals: list[OpenAIApprovalRequest] = []
    loops = 0
    while getattr(result, "interruptions", None):
        loops += 1
        if loops > 10:
            raise OpenAIAgentFactoryError("Too many OpenAI approval interruption loops")
        if approval_callback is None:
            raise OpenAIAgentFactoryError("OpenAI approval interruption requires an approval_callback")
        state = result.to_state()
        for interruption in result.interruptions:
            tool_name = contract_tool_name(str(getattr(interruption, "tool_name", "")))
            arguments = _approval_arguments(interruption)
            trace.record("approval.requested", tool=tool_name, arguments=arguments)
            approved = bool(await _maybe_await(approval_callback(OpenAIApprovalRequest(tool_name, None, arguments))))
            trace.record("approval.completed", tool=tool_name, approved=approved)
            approvals.append(OpenAIApprovalRequest(tool_name, approved, arguments))
            if approved:
                state.approve(interruption)
            else:
                state.reject(interruption, rejection_message=f"Approval denied for {tool_name}")
        result = await runner.run(agent, state, context=context, max_turns=max_turns, hooks=hooks)
    return result, approvals


def _approval_arguments(interruption: Any) -> dict[str, Any]:
    for attr in ("arguments", "tool_arguments", "input"):
        value = getattr(interruption, attr, None)
        if isinstance(value, dict):
            return dict(value)
    return {}


async def _maybe_await(value: bool | Awaitable[bool]) -> bool:
    if inspect.isawaitable(value):
        return bool(await value)
    return bool(value)


def _record_assertion_events(trace: TraceRecorder, result: RunEvaluationResult) -> None:
    for agent_result in result.agents:
        for check in agent_result.checks:
            data: dict[str, Any] = {"status": check.status}
            if check.failure is not None:
                data["failure_kind"] = check.failure.kind
                data["message"] = check.failure.message
            trace.record("assertion.evaluated", agent=agent_result.agent, assertion=check.assertion, data=data)


__all__ = ["run_openai_agent", "run_openai_agent_with_contract"]
