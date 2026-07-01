"""Production monitor rule checks over normalized traces."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents.expressions._eval import evaluate_trace
from contract4agents.expressions._grammar import parse_monitor_condition, parse_monitor_expectation
from contract4agents.expressions._model import ExpressionError
from contract4agents.runtime import TraceEvent, TraceRecorder


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


def run_monitors(rules: list[MonitorRule], trace: TraceRecorder) -> list[MonitorViolation]:
    violations: list[MonitorViolation] = []
    for rule in rules:
        try:
            condition = parse_monitor_condition(rule.when)
            expectation = parse_monitor_expectation(rule.expect)
        except ExpressionError as exc:
            violations.append(MonitorViolation(rule.name, rule.severity, f"Invalid monitor rule: {exc}"))
            continue
        scoped_trace = _trace_for_agent(trace, rule.agent)
        condition_failure = evaluate_trace(condition, scoped_trace) if condition else None
        if condition_failure:
            continue
        expectation_failure = evaluate_trace(expectation, scoped_trace) if expectation else None
        if expectation_failure:
            violations.append(
                MonitorViolation(rule.name, rule.severity, f"Monitor `{rule.name}` failed: {rule.expect}")
            )
    return violations


def _trace_for_agent(trace: TraceRecorder, agent: str) -> TraceRecorder:
    scoped = TraceRecorder(run_id=trace.run_id)
    scoped.events = [event for event in trace.events if _matches_agent(event, agent)]
    return scoped


def _matches_agent(event: TraceEvent, agent: str) -> bool:
    event_agent = event.data.get("agent")
    return event_agent is None or event_agent == agent
