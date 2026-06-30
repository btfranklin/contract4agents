from __future__ import annotations

from contract4agents.runtime import ContextValue, DatasourceContext
from tests.fixtures.ops_desk_lab.data import fetch_one


def resolve_customer_account(ctx: DatasourceContext) -> ContextValue:
    request = ctx.get("StartRequest").value
    account = fetch_one(
        "SELECT account_id, name, status, plan, risk FROM accounts WHERE account_id = ?",
        (request["account_id"],),
    ) or {
        "account_id": request["account_id"],
        "name": "unknown",
        "status": "missing",
        "plan": "unknown",
        "risk": "unknown",
    }
    rendered = (
        f"account_id: {account['account_id']}\n"
        f"name: {account['name']}\n"
        f"status: {account['status']}\n"
        f"plan: {account['plan']}\n"
        f"risk: {account['risk']}"
    )
    return ContextValue("CustomerAccount", account, rendered, "CustomerAccountSource")


def resolve_request_intent(ctx: DatasourceContext) -> ContextValue:
    request = ctx.get("StartRequest").value
    message = request["message"].lower()
    if "hidden_truth" in message or "ignore approval" in message:
        intent = {"route": "rejected", "action": "prompt_injection", "requires_approval": False}
    elif request["account_id"] == "acct_missing":
        intent = {"route": "unknown", "action": "account_lookup", "requires_approval": False}
    elif "invoice" in message or "charged" in message or "credit" in message:
        wants_credit = "credit" in message or "charged twice" in message
        intent = {
            "route": "billing",
            "action": "credit" if wants_credit else "explain",
            "requires_approval": wants_credit,
        }
    elif "lock" in message or "suspicious" in message or "sign-in" in message:
        wants_lock = "lock" in message
        intent = {"route": "security", "action": "lock" if wants_lock else "audit", "requires_approval": wants_lock}
    elif "permission" in message or "admin" in message or "access" in message:
        wants_grant = "grant" in message or "admin" in message
        intent = {"route": "access", "action": "grant" if wants_grant else "list", "requires_approval": wants_grant}
    elif "mfa" in message or "reset" in message:
        intent = {"route": "knowledge", "action": "search", "requires_approval": False}
    else:
        intent = {"route": "unknown", "action": "unknown", "requires_approval": False}
    rendered = (
        f"route: {intent['route']}\n"
        f"action: {intent['action']}\n"
        f"requires_approval: {intent['requires_approval']}"
    )
    return ContextValue("RequestIntent", intent, rendered, "RequestIntentSource")
