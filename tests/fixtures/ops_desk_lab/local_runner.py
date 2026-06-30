from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contract4agents.runtime import (
    ContextValue,
    DatasourceRegistry,
    DatasourceSpec,
    FakeToolRegistry,
    RuntimeContext,
    ToolPermissionDenied,
    TraceRecorder,
    load_python_ref,
)
from tests.fixtures.ops_desk_lab import tools as fake_tools
from tests.fixtures.ops_desk_lab.data import set_db_path
from tests.fixtures.ops_desk_lab.scenarios import OpsDeskStart


async def run_ops_desk_start(
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
    request = runtime.values["StartRequest"].value
    account = runtime.values["CustomerAccount"].value
    intent = runtime.values["RequestIntent"].value
    trace.record("agent.started", agent="OpsDeskCoordinator")
    output = await _route(start, request, account, intent, trace)
    output = _customer_reply_writer(output, trace)
    trace.record("agent.completed", agent="OpsDeskCoordinator", output=output)
    return output, trace


def _datasource_registry(artifacts: dict[str, Any]) -> DatasourceRegistry:
    registry = DatasourceRegistry()
    coordinator = artifacts["manifests"]["OpsDeskCoordinator"]
    for item in coordinator["datasources"]:
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


async def _route(
    start: OpsDeskStart,
    request: dict[str, Any],
    account: dict[str, Any],
    intent: dict[str, Any],
    trace: TraceRecorder,
) -> dict[str, Any]:
    if intent["route"] == "rejected":
        trace.record("guardrail.rejected", guardrail="prompt_injection", start_id=start.start_id)
        return _result(
            start,
            "rejected",
            "rejected",
            "Request rejected because it attempted to bypass approval rules.",
            [],
            [],
        )
    if intent["route"] == "unknown" or account["status"] == "missing":
        return _result(start, "unknown", "not_found", f"account {request['account_id']} could not be found.", [], [])
    registry = _tool_registry(start)
    if intent["route"] == "billing":
        return await _billing(start, account, intent, registry, trace)
    if intent["route"] == "security":
        return await _security(start, account, intent, registry, trace)
    if intent["route"] == "access":
        return await _access(start, account, intent, registry, trace)
    if intent["route"] == "knowledge":
        return await _knowledge(start, registry, trace)
    return _result(start, "unknown", "not_found", "No supported route matched the request.", [], [])


def _tool_registry(start: OpsDeskStart) -> FakeToolRegistry:
    registry = FakeToolRegistry(approval_callback=lambda name, _kwargs: start.approvals.get(name, False))
    registry.register("billing.lookup_invoice", fake_tools.billing_lookup_invoice, "preapproved")
    registry.register("billing.create_credit", fake_tools.billing_create_credit, "requires_approval")
    registry.register("security.audit_log", fake_tools.security_audit_log, "preapproved")
    registry.register("security.lock_account", fake_tools.security_lock_account, "requires_approval")
    registry.register("access.list_permissions", fake_tools.access_list_permissions, "preapproved")
    registry.register("access.grant_access", fake_tools.access_grant_access, "requires_approval")
    registry.register("knowledge.search", fake_tools.knowledge_search, "preapproved")
    return registry


async def _billing(
    start: OpsDeskStart,
    account: dict[str, Any],
    intent: dict[str, Any],
    registry: FakeToolRegistry,
    trace: TraceRecorder,
) -> dict[str, Any]:
    trace.record("agent.started", agent="BillingSpecialist")
    raw = await registry.call("billing.lookup_invoice", trace, account_id=account["account_id"])
    invoices = json.loads(raw)["invoices"]
    if intent["action"] == "credit":
        duplicate = next(item for item in invoices if item["status"] == "duplicate")
        try:
            await registry.call(
                "billing.create_credit",
                trace,
                account_id=account["account_id"],
                invoice_id=duplicate["invoice_id"],
                amount=duplicate["amount"],
                reason="duplicate charge",
            )
        except ToolPermissionDenied:
            output = _result(
                start,
                "billing",
                "needs_approval",
                f"Invoice {duplicate['invoice_id']} is duplicate, but credit needs approval before it can be created.",
                [duplicate["summary"]],
                ["request billing approval"],
            )
        else:
            output = _result(
                start,
                "billing",
                "resolved",
                f"A credit was created for duplicate invoice {duplicate['invoice_id']}.",
                [duplicate["summary"]],
                ["created billing credit"],
            )
    else:
        invoice = next(item for item in invoices if item["invoice_id"] == "INV-100")
        output = _result(
            start,
            "billing",
            "resolved",
            f"Invoice {invoice['invoice_id']} is paid: {invoice['summary']}.",
            [invoice["summary"]],
            ["explained invoice"],
        )
    trace.record("agent.completed", agent="BillingSpecialist", output=output)
    return output


async def _security(
    start: OpsDeskStart,
    account: dict[str, Any],
    intent: dict[str, Any],
    registry: FakeToolRegistry,
    trace: TraceRecorder,
) -> dict[str, Any]:
    trace.record("agent.started", agent="SecuritySpecialist")
    raw = await registry.call("security.audit_log", trace, account_id=account["account_id"])
    events = json.loads(raw)["events"]
    evidence = [item["summary"] for item in events]
    if intent["action"] == "lock":
        try:
            await registry.call(
                "security.lock_account",
                trace,
                account_id=account["account_id"],
                reason="suspicious login",
            )
        except ToolPermissionDenied:
            output = _result(
                start,
                "security",
                "needs_approval",
                "Security evidence supports locking the account, but lock approval was denied.",
                evidence,
                ["request security approval"],
            )
        else:
            output = _result(
                start,
                "security",
                "resolved",
                "The account was locked after approved suspicious-login escalation.",
                evidence,
                ["locked account"],
            )
    else:
        output = _result(
            start,
            "security",
            "resolved",
            "Audit evidence shows impossible travel and elevated account risk.",
            evidence,
            ["recommend password reset"],
        )
    trace.record("agent.completed", agent="SecuritySpecialist", output=output)
    return output


async def _access(
    start: OpsDeskStart,
    account: dict[str, Any],
    intent: dict[str, Any],
    registry: FakeToolRegistry,
    trace: TraceRecorder,
) -> dict[str, Any]:
    trace.record("agent.started", agent="AccessSpecialist")
    raw = await registry.call("access.list_permissions", trace, account_id=account["account_id"])
    permissions = json.loads(raw)["permissions"]
    evidence = [f"{item['entitlement']}={item['status']}" for item in permissions]
    if intent["action"] == "grant":
        try:
            await registry.call("access.grant_access", trace, account_id=account["account_id"], entitlement="admin")
        except ToolPermissionDenied:
            output = _result(
                start,
                "access",
                "needs_approval",
                "Admin access needs approval before it can be granted.",
                evidence,
                ["request access approval"],
            )
        else:
            output = _result(
                start,
                "access",
                "resolved",
                "Admin access was granted after approval.",
                evidence,
                ["granted admin access"],
            )
    else:
        output = _result(
            start,
            "access",
            "resolved",
            "Current permissions show reports enabled and admin disabled.",
            evidence,
            ["reported permissions"],
        )
    trace.record("agent.completed", agent="AccessSpecialist", output=output)
    return output


async def _knowledge(start: OpsDeskStart, registry: FakeToolRegistry, trace: TraceRecorder) -> dict[str, Any]:
    trace.record("agent.started", agent="KnowledgeSpecialist")
    raw = await registry.call("knowledge.search", trace, query="MFA reset")
    articles = json.loads(raw)["articles"]
    evidence = [item["title"] for item in articles]
    output = _result(
        start,
        "knowledge",
        "resolved",
        "MFA reset requires owner confirmation, then Settings > Security > Reset MFA.",
        evidence,
        ["shared MFA reset article"],
    )
    trace.record("agent.completed", agent="KnowledgeSpecialist", output=output)
    return output


def _customer_reply_writer(output: dict[str, Any], trace: TraceRecorder) -> dict[str, Any]:
    trace.record("agent.started", agent="CustomerReplyWriter")
    output["reply"] = output["reply"].replace("  ", " ").strip()
    trace.record("agent.completed", agent="CustomerReplyWriter", output=output)
    return output


def _result(
    start: OpsDeskStart,
    route: str,
    status: str,
    reply: str,
    evidence: list[str],
    actions: list[str],
) -> dict[str, Any]:
    return {
        "start_id": start.start_id,
        "route": route,
        "status": status,
        "reply": reply,
        "evidence": evidence,
        "actions": actions,
    }
