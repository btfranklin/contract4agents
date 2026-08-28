"""Small, explicit grammar for single-run operational controls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from contract4agents.expressions._model import ExpressionError

OperationalMetric = Literal[
    "duration",
    "provider_request_count",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "failed_provider_call_count",
    "attempt_count",
    "retry_count",
]

_METRIC_ALIASES: dict[str, OperationalMetric] = {
    "duration": "duration",
    "provider_requests": "provider_request_count",
    "provider_request_count": "provider_request_count",
    "request_count": "provider_request_count",
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "total_tokens": "total_tokens",
    "failed_provider_calls": "failed_provider_call_count",
    "failed_provider_call_count": "failed_provider_call_count",
    "attempt_count": "attempt_count",
    "attempts": "attempt_count",
    "retry_count": "retry_count",
    "retries": "retry_count",
}
_RE = re.compile(
    r"^trace\.(?P<metric>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?P<operator>==|!=|<=|>=|<|>)\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<unit>ms|s)?$"
)


@dataclass(frozen=True)
class ParsedOperationalRequirement:
    expression: str
    metric: OperationalMetric
    operator: str
    target: float
    unit: str | None = None


def parse_operational_requirement(expression: str) -> ParsedOperationalRequirement:
    """Parse the supported single-run metric comparison forms.

    Window aggregation and arbitrary telemetry queries are intentionally not
    part of this grammar.  A caller must reject them as unsupported.
    """

    value = expression.strip()
    match = _RE.fullmatch(value)
    if match is None:
        raise ExpressionError(f"Unsupported operational expression: {expression}")
    raw_metric = match.group("metric")
    metric = _METRIC_ALIASES.get(raw_metric)
    if metric is None:
        raise ExpressionError(f"Unsupported operational metric `{raw_metric}`")
    unit = match.group("unit")
    target = float(match.group("value"))
    if metric != "duration" and unit is not None:
        raise ExpressionError(f"Only duration accepts a unit; got `{unit}`")
    if metric == "duration" and unit == "ms":
        target /= 1000
        unit = "s"
    return ParsedOperationalRequirement(value, metric, match.group("operator"), target, unit)


__all__ = ["OperationalMetric", "ParsedOperationalRequirement", "parse_operational_requirement"]
