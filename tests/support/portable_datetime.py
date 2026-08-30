"""Shared portable date-time cases for runtime parity tests."""

from __future__ import annotations

PORTABLE_DATETIME_CASES: tuple[tuple[str, object, bool], ...] = (
    ("UTC Z", "2026-01-01T00:00:00Z", True),
    ("positive offset", "2026-01-01T00:00:00+05:30", True),
    ("negative offset", "2026-01-01T00:00:00-07:00", True),
    ("fractional seconds", "2026-01-01T00:00:00.123456Z", True),
    ("naive", "2026-01-01T00:00:00", False),
    ("space separator", "2026-01-01 00:00:00Z", False),
    ("impossible date", "2026-02-30T00:00:00Z", False),
    ("invalid offset hour", "2026-01-01T00:00:00+24:00", False),
    ("invalid offset minute", "2026-01-01T00:00:00+01:60", False),
    ("non-string", 20260101, False),
)

__all__ = ["PORTABLE_DATETIME_CASES"]
