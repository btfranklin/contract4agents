from __future__ import annotations

from contract4agents.guards import build_guard_plan


def test_guard_plan_classifies_output_conformance() -> None:
    plan = build_guard_plan(
        {
            "AgentA": {
                "tools": [],
                "guards": ["require(output conforms Result)"],
            }
        }
    )

    assert plan == [
        {
            "agent": "AgentA",
            "expression": "require(output conforms Result)",
            "kind": "output_conformance",
            "status": "supported",
            "enforcement": "output_schema",
            "target": None,
            "output_type": "Result",
            "declared_permission": None,
            "message": "Output must conform to `Result`.",
        }
    ]


def test_guard_plan_classifies_approval_required_tool() -> None:
    plan = build_guard_plan(
        {
            "AgentA": {
                "tools": [{"name": "billing.create_credit", "permission": "requires_approval"}],
                "guards": ["forbid(tool.billing.create_credit unless approved_by_human)"],
            }
        }
    )

    assert plan[0]["kind"] == "approval_required_tool"
    assert plan[0]["enforcement"] == "host_approval_required"
    assert plan[0]["target"] == "billing.create_credit"
    assert plan[0]["declared_permission"] == "requires_approval"


def test_guard_plan_classifies_denied_tool() -> None:
    plan = build_guard_plan(
        {
            "AgentA": {
                "tools": [{"name": "github.merge_pull_request", "permission": "denied"}],
                "guards": ["forbid(tool.github.merge_pull_request)"],
            }
        }
    )

    assert plan[0]["kind"] == "denied_tool"
    assert plan[0]["enforcement"] == "adapter_tool_omission"
    assert plan[0]["target"] == "github.merge_pull_request"
    assert plan[0]["declared_permission"] == "denied"


def test_guard_plan_reports_parseable_unsupported_guards() -> None:
    plan = build_guard_plan(
        {
            "AgentA": {
                "tools": [],
                "guards": ["expect(output.ok == true)"],
            }
        }
    )

    assert plan[0]["kind"] == "unsupported"
    assert plan[0]["status"] == "unsupported"
    assert plan[0]["enforcement"] == "unsupported"
    assert "no supported enforcement mapping" in str(plan[0]["message"])
