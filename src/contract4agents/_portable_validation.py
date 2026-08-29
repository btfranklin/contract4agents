"""Shared validators for portable values that need runtime-specific care."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime

from jsonschema import Draft202012Validator, FormatChecker

_DATETIME_PATTERN = re.compile(
    r"\A(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.[0-9]+)?(?:Z|(?P<offset_sign>[+-])(?P<offset_hour>[0-9]{2}):(?P<offset_minute>[0-9]{2}))\Z"
)
_DATETIME_ERROR = "Expected RFC 3339 datetime with a required Z or ±HH:MM offset"
_FORMAT_CHECKER = FormatChecker()


def parse_portable_datetime(value: object) -> datetime:
    """Parse one portable RFC 3339 datetime and return an aware datetime."""

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(_DATETIME_ERROR)
        return value
    if not isinstance(value, str):
        raise ValueError(_DATETIME_ERROR)
    match = _DATETIME_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(_DATETIME_ERROR)
    parts = match.groupdict()
    year = int(parts["year"])
    month = int(parts["month"])
    day = int(parts["day"])
    hour = int(parts["hour"])
    minute = int(parts["minute"])
    second = int(parts["second"])
    offset_hour = int(parts["offset_hour"] or 0)
    offset_minute = int(parts["offset_minute"] or 0)
    if (
        year == 0
        or month < 1
        or month > 12
        or day < 1
        or hour > 23
        or minute > 59
        or second > 59
        or offset_hour > 23
        or offset_minute > 59
    ):
        raise ValueError(_DATETIME_ERROR)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(_DATETIME_ERROR) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(_DATETIME_ERROR)
    return parsed


def is_portable_datetime(value: object) -> bool:
    """Return whether a value is accepted by the portable datetime profile."""

    try:
        parse_portable_datetime(value)
    except (TypeError, ValueError):
        return False
    return True


@_FORMAT_CHECKER.checks("date-time")
def _is_portable_json_datetime(value: object) -> bool:
    return not isinstance(value, str) or is_portable_datetime(value)


def validate_portable_json_schema(
    value: object,
    schema: Mapping[str, object],
) -> None:
    """Validate one value with the portable JSON Schema format profile."""

    Draft202012Validator(schema, format_checker=_FORMAT_CHECKER).validate(value)


__all__ = [
    "is_portable_datetime",
    "parse_portable_datetime",
    "validate_portable_json_schema",
]
