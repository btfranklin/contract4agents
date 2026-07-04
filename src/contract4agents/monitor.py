"""Production monitor rule checks over normalized traces."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents.expressions._eval import evaluate_trace
from contract4agents.expressions._grammar import parse_monitor_condition, parse_monitor_expectation
from contract4agents.expressions._model import ExpressionError
from contract4agents.runtime import TraceRecorder, scope_trace


@dataclass(frozen=True)
class MonitorRule:
    name: str
    agent: str
    severity: str
    when: str
    expect: str


@dataclass(frozen=True)
class MonitorViolation:
    rule: str
    severity: str
    message: str
    agent: str = ""
    run_id: str = ""
    condition: str = ""
    expectation: str = ""


def run_monitors(
    rules: list[MonitorRule],
    trace: TraceRecorder,
    *,
    run_id: str | None = None,
) -> list[MonitorViolation]:
    violations: list[MonitorViolation] = []
    for rule in rules:
        try:
            condition = parse_monitor_condition(rule.when)
            expectation = parse_monitor_expectation(rule.expect)
        except ExpressionError as exc:
            violations.append(
                MonitorViolation(
                    rule.name,
                    rule.severity,
                    f"Invalid monitor rule: {exc}",
                    rule.agent,
                    run_id or "",
                    rule.when,
                    rule.expect,
                )
            )
            continue
        scoped_trace = scope_trace(trace, run_id=run_id, agent=rule.agent, strict_agent_scope=True)
        condition_failure = evaluate_trace(condition, scoped_trace) if condition else None
        if condition_failure:
            continue
        expectation_failure = evaluate_trace(expectation, scoped_trace) if expectation else None
        if expectation_failure:
            violations.append(
                MonitorViolation(
                    rule.name,
                    rule.severity,
                    f"Monitor `{rule.name}` failed: {rule.expect}",
                    rule.agent,
                    scoped_trace.run_id,
                    rule.when,
                    rule.expect,
                )
            )
    return violations
