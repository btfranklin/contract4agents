"""Structured guard-plan helpers for compiled Contract4Agents artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from contract4agents.expressions._grammar import parse_contract_expression
from contract4agents.expressions._model import ConditionalExpression, ExpressionError

GuardKind = Literal["output_conformance", "approval_required_tool", "denied_tool", "unsupported"]
GuardStatus = Literal["supported", "unsupported"]
GuardEnforcement = Literal["output_schema", "host_approval_required", "adapter_tool_omission", "unsupported"]


class GuardPlanItem(TypedDict):
    agent: str
    expression: str
    kind: GuardKind
    status: GuardStatus
    enforcement: GuardEnforcement
    target: str | None
    output_type: str | None
    declared_permission: str | None
    message: str | None


def build_guard_plan(manifests: Mapping[str, Mapping[str, Any]]) -> list[GuardPlanItem]:
    """Build a JSON-serializable host/adaptor enforcement plan from compiled manifests."""
    items: list[GuardPlanItem] = []
    for agent_name, manifest in manifests.items():
        tool_permissions = {
            str(tool["name"]): str(tool.get("permission", "available"))
            for tool in manifest.get("tools", [])
            if isinstance(tool, Mapping) and "name" in tool
        }
        for expression in manifest.get("guards", []):
            items.append(_classify_guard(str(agent_name), str(expression), tool_permissions))
    return items


def _classify_guard(agent: str, expression: str, tool_permissions: Mapping[str, str]) -> GuardPlanItem:
    try:
        parsed_items = parse_contract_expression(expression)
    except ExpressionError as exc:
        return _unsupported(agent, expression, str(exc))
    if len(parsed_items) != 1:
        return _unsupported(agent, expression, "Compound guard expressions are not supported")
    parsed = parsed_items[0]
    if isinstance(parsed, ConditionalExpression):
        return _unsupported(agent, expression, "Conditional guard expressions are not supported")
    if parsed.wrapper == "require" and parsed.kind == "output_conforms" and parsed.type_name:
        return _item(
            agent,
            expression,
            "output_conformance",
            "supported",
            "output_schema",
            output_type=parsed.type_name,
            message=f"Output must conform to `{parsed.type_name}`.",
        )
    if parsed.wrapper == "forbid" and parsed.trace_op == "tool_called" and parsed.args:
        target = parsed.args[0]
        permission = tool_permissions.get(target)
        if parsed.approval_required:
            return _item(
                agent,
                expression,
                "approval_required_tool",
                "supported",
                "host_approval_required",
                target=target,
                declared_permission=permission,
                message=f"Host code must require approval before `{target}` is called.",
            )
        return _item(
            agent,
            expression,
            "denied_tool",
            "supported",
            "adapter_tool_omission",
            target=target,
            declared_permission=permission,
            message=f"Adapters must omit `{target}` from callable tools.",
        )
    return _unsupported(agent, expression, "Guard syntax is valid but has no supported enforcement mapping")


def _item(
    agent: str,
    expression: str,
    kind: GuardKind,
    status: GuardStatus,
    enforcement: GuardEnforcement,
    *,
    target: str | None = None,
    output_type: str | None = None,
    declared_permission: str | None = None,
    message: str | None = None,
) -> GuardPlanItem:
    return {
        "agent": agent,
        "expression": expression,
        "kind": kind,
        "status": status,
        "enforcement": enforcement,
        "target": target,
        "output_type": output_type,
        "declared_permission": declared_permission,
        "message": message,
    }


def _unsupported(agent: str, expression: str, message: str) -> GuardPlanItem:
    return _item(agent, expression, "unsupported", "unsupported", "unsupported", message=message)


__all__ = [
    "GuardEnforcement",
    "GuardKind",
    "GuardPlanItem",
    "GuardStatus",
    "build_guard_plan",
]
