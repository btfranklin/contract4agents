from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from contract4agents.adapters.openai import (
    OpenAITraceHooks,
    build_openai_agent,
    build_openai_agents_from_contracts,
    contract_tool_name,
    openai_tool_name,
)
from contract4agents.runtime import (
    ContextValue,
    DatasourceRegistry,
    DatasourceSpec,
    RuntimeContext,
    TraceRecorder,
    load_python_ref,
)
from tests.fixtures.ops_desk_lab import tools as fake_tools
from tests.fixtures.ops_desk_lab.data import set_db_path
from tests.fixtures.ops_desk_lab.scenarios import OpsDeskStart


class OpsDeskResultModel(BaseModel):
    start_id: str
    route: Literal["billing", "security", "access", "knowledge", "rejected", "unknown"]
    status: Literal["resolved", "needs_approval", "rejected", "not_found"]
    reply: str
    evidence: list[str]
    actions: list[str]


async def run_ops_desk_start_live(
    start: OpsDeskStart,
    db_path: Path,
    artifacts: dict[str, Any],
    trace_path: Path,
) -> tuple[dict[str, Any], TraceRecorder]:
    set_db_path(db_path)
    trace = TraceRecorder(trace_path)
    runtime = RuntimeContext(
        values={
            "StartRequest": ContextValue(
                "StartRequest",
                {"start_id": start.start_id, "message": start.message, "account_id": start.account_id},
                f"start_id: {start.start_id}\naccount_id: {start.account_id}\nmessage: {start.message}",
                "fixture_start",
            )
        },
        trace=trace,
    )
    datasource_names = {item["name"] for item in artifacts["manifests"]["OpsDeskCoordinator"]["datasources"]}
    await runtime.resolve(["CustomerAccount", "RequestIntent"], _datasource_registry(artifacts), datasource_names)
    if runtime.values["RequestIntent"].value["route"] == "rejected":
        trace.record("guardrail.rejected", guardrail="prompt_injection", start_id=start.start_id)
        return _rejected_output(start), trace

    try:
        from agents import (
            GuardrailFunctionOutput,
            InputGuardrailTripwireTriggered,
            Runner,
            function_tool,
            input_guardrail,
        )
    except Exception as exc:  # noqa: BLE001 - optional SDK import boundary for opt-in tests.
        raise RuntimeError("openai-agents is not available") from exc

    hooks = OpenAITraceHooks(trace)
    sdk_tools = _sdk_tools(function_tool, trace)
    agents = _build_agents(artifacts, sdk_tools, hooks)

    @input_guardrail(name="prompt_injection", run_in_parallel=False)
    async def prompt_injection_guardrail(_ctx: Any, _agent: Any, input_data: str | list[Any]) -> Any:
        text = str(input_data).lower()
        rejected = "hidden_truth" in text or "ignore approval" in text
        if rejected:
            trace.record("guardrail.rejected", guardrail="prompt_injection", start_id=start.start_id)
        return GuardrailFunctionOutput(output_info={"rejected": rejected}, tripwire_triggered=rejected)

    coordinator_manifest = dict(artifacts["manifests"]["OpsDeskCoordinator"])
    coordinator_manifest["model"] = os.getenv("CONTRACT4AGENTS_OPENAI_AGENT_MODEL", "gpt-5.5")
    coordinator = build_openai_agent(
        coordinator_manifest,
        _coordinator_instructions(artifacts["instructions"]["OpsDeskCoordinator"]),
        tools=[
            agents["BillingSpecialist"].as_tool(
                tool_name="billing_specialist",
                tool_description=(
                    "Resolve billing requests. Input must include account_id, start_id, message, and action. "
                    "For duplicate credit requests, attempt the approval-gated credit tool."
                ),
                hooks=hooks,
                max_turns=6,
            ),
            agents["SecuritySpecialist"].as_tool(
                tool_name="security_specialist",
                tool_description=(
                    "Resolve security requests. Input must include account_id, start_id, message, and action. "
                    "For lock requests, attempt the approval-gated lock tool."
                ),
                hooks=hooks,
                max_turns=6,
            ),
            agents["AccessSpecialist"].as_tool(
                tool_name="access_specialist",
                tool_description=(
                    "Resolve access requests. Input must include account_id, start_id, message, and action. "
                    "For grant requests, attempt the approval-gated grant tool."
                ),
                hooks=hooks,
                max_turns=6,
            ),
            agents["KnowledgeSpecialist"].as_tool(
                tool_name="knowledge_specialist",
                tool_description="Resolve knowledge-base requests. Input must include start_id and message.",
                hooks=hooks,
                max_turns=6,
            ),
            sdk_tools["billing.create_credit"],
            sdk_tools["security.lock_account"],
            sdk_tools["access.grant_access"],
        ],
        output_type=OpsDeskResultModel,
        input_guardrails=[prompt_injection_guardrail],
    )
    prompt = _prompt(start, runtime)
    try:
        result = await Runner.run(coordinator, prompt, max_turns=8, hooks=hooks)
    except InputGuardrailTripwireTriggered:
        return _rejected_output(start), trace

    result = await _resolve_approvals(Runner, coordinator, result, start, trace, hooks)
    return _coerce_output(result.final_output), trace


def _datasource_registry(artifacts: dict[str, Any]) -> DatasourceRegistry:
    registry = DatasourceRegistry()
    for item in artifacts["manifests"]["OpsDeskCoordinator"]["datasources"]:
        registry.register(
            item["name"],
            DatasourceSpec(
                item["name"],
                item["produces"],
                list(item["requires"]),
                load_python_ref(item["python"]),
                cache=item["cache"],
            ),
        )
    return registry


def _sdk_tools(function_tool: Any, trace: TraceRecorder) -> dict[str, Any]:
    @function_tool(name_override=openai_tool_name("billing.lookup_invoice"))
    def billing_lookup_invoice(account_id: str, invoice_id: str = "") -> str:
        return fake_tools.billing_lookup_invoice(account_id, invoice_id)

    @function_tool(name_override=openai_tool_name("billing.create_credit"), needs_approval=True)
    def billing_create_credit(account_id: str, invoice_id: str, amount: float, reason: str) -> str:
        return fake_tools.billing_create_credit(account_id, invoice_id, amount, reason)

    @function_tool(name_override=openai_tool_name("security.audit_log"))
    def security_audit_log(account_id: str) -> str:
        return fake_tools.security_audit_log(account_id)

    @function_tool(name_override=openai_tool_name("security.lock_account"), needs_approval=True)
    def security_lock_account(account_id: str, reason: str) -> str:
        return fake_tools.security_lock_account(account_id, reason)

    @function_tool(name_override=openai_tool_name("access.list_permissions"))
    def access_list_permissions(account_id: str) -> str:
        return fake_tools.access_list_permissions(account_id)

    @function_tool(name_override=openai_tool_name("access.grant_access"), needs_approval=True)
    def access_grant_access(account_id: str, entitlement: str) -> str:
        return fake_tools.access_grant_access(account_id, entitlement)

    @function_tool(name_override=openai_tool_name("knowledge.search"))
    def knowledge_search(query: str) -> str:
        return fake_tools.knowledge_search(query)

    return {
        "billing.lookup_invoice": billing_lookup_invoice,
        "billing.create_credit": billing_create_credit,
        "security.audit_log": security_audit_log,
        "security.lock_account": security_lock_account,
        "access.list_permissions": access_list_permissions,
        "access.grant_access": access_grant_access,
        "knowledge.search": knowledge_search,
    }


def _build_agents(artifacts: dict[str, Any], tools: dict[str, Any], hooks: OpenAITraceHooks) -> dict[str, Any]:
    model = os.getenv("CONTRACT4AGENTS_OPENAI_AGENT_MODEL", "gpt-5.5")
    result = build_openai_agents_from_contracts(
        artifacts,
        output_type_registry={"OpsDeskResult": OpsDeskResultModel},
        model_registry={},
        tool_registry=tools,
        instruction_overrides={
            name: _specialist_instructions(name, artifacts["instructions"][name])
            for name in [
                "BillingSpecialist",
                "SecuritySpecialist",
                "AccessSpecialist",
                "KnowledgeSpecialist",
            ]
        },
        default_model=model,
    )
    return result.agents


async def _resolve_approvals(
    runner: Any,
    coordinator: Any,
    result: Any,
    start: OpsDeskStart,
    trace: TraceRecorder,
    hooks: OpenAITraceHooks,
) -> Any:
    loops = 0
    while getattr(result, "interruptions", None):
        loops += 1
        if loops > 3:
            raise RuntimeError("too many approval interruption loops")
        state = result.to_state()
        for interruption in result.interruptions:
            tool_name = contract_tool_name(interruption.tool_name)
            approved = start.approvals.get(tool_name, False)
            trace.record("approval.completed", tool=tool_name, approved=approved)
            if approved:
                state.approve(interruption)
            else:
                state.reject(interruption, rejection_message=f"Approval denied for {tool_name}")
        result = await runner.run(coordinator, state, max_turns=8, hooks=hooks)
    return result


def _coordinator_instructions(base: str) -> str:
    return (
        f"{base}\n"
        "Use exactly one specialist tool for billing, security, access, or knowledge routes. "
        "For unknown accounts, do not call specialist tools. "
        "For approval routes, call the specialist first, then call the matching approval-gated action tool yourself. "
        "Use billing__create_credit for duplicate billing credits, security__lock_account for lock requests, "
        "and access__grant_access for admin grants. Return only an OpsDeskResult."
    )


def _specialist_instructions(name: str, base: str) -> str:
    extra = {
        "BillingSpecialist": (
            "Use billing__lookup_invoice first. If the action is credit or the message says duplicate/charged twice, "
            "you must call billing__create_credit. If approval is denied, return status needs_approval."
        ),
        "SecuritySpecialist": (
            "Use security__audit_log first. If the action is lock, you must call security__lock_account. "
            "If approval is denied, return status needs_approval."
        ),
        "AccessSpecialist": (
            "Use access__list_permissions first. If the action is grant or the message asks for admin access, "
            "you must call access__grant_access. If approval is denied, return status needs_approval."
        ),
        "KnowledgeSpecialist": (
            "Use knowledge__search before answering. Query with the shortest key phrase, such as MFA."
        ),
    }[name]
    return f"{base}\n{extra}\nReturn only an OpsDeskResult for the provided start_id."


def _prompt(start: OpsDeskStart, runtime: RuntimeContext) -> str:
    return (
        "Run this Contract4Agents fixture start.\n"
        f"start_id: {start.start_id}\n"
        f"account_id: {start.account_id}\n"
        f"message: {start.message}\n\n"
        "Resolved context:\n"
        f"{runtime.rendered_context()}\n\n"
        "Rules:\n"
        "- Match output.start_id exactly.\n"
        "- If route is unknown, set status not_found and do not call specialist tools.\n"
        "- If RequestIntent.requires_approval is true, call the specialist first, then attempt the approval-gated "
        "tool yourself.\n"
        "- If an approval-gated tool is denied, set status needs_approval and mention approval in reply.\n"
        "- If an approval-gated tool is approved and executed, set status resolved.\n"
    )


def _coerce_output(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return json.loads(str(value))


def _rejected_output(start: OpsDeskStart) -> dict[str, Any]:
    return {
        "start_id": start.start_id,
        "route": "rejected",
        "status": "rejected",
        "reply": "Request rejected because it attempted to bypass approval rules.",
        "evidence": [],
        "actions": [],
    }
