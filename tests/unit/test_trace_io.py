from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract4agents.monitor import MonitorRule, run_monitors
from contract4agents.runtime import (
    TRACE_SCHEMA_VERSION,
    TraceFileError,
    TraceRecorder,
    load_trace_jsonl,
    load_trace_jsonl_with_diagnostics,
)


def _event(
    *,
    schema_version: str = TRACE_SCHEMA_VERSION,
    run_id: str = "run-test",
    event_id: str = "evt-1",
    event_type: str = "tool.completed",
    timestamp: object = 1.0,
    agent: object | None = None,
    tool: object | None = None,
    data: object | None = None,
    provider: object | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": schema_version,
        "run_id": run_id,
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "data": data if data is not None else {},
        "provider": provider if provider is not None else {},
    }
    if agent is not None:
        payload["agent"] = agent
    if tool is not None:
        payload["tool"] = tool
    return payload


def test_trace_recorder_writes_canonical_envelope_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    trace = TraceRecorder(path, run_id="run-test")

    trace.record(
        "tool.completed",
        event_id="evt-test",
        timestamp=1.5,
        tool="logs.search",
        result={"count": 2},
        provider={"sdk": "fake"},
    )

    payload = json.loads(path.read_text())
    assert payload == {
        "schema_version": TRACE_SCHEMA_VERSION,
        "run_id": "run-test",
        "event_id": "evt-test",
        "event_type": "tool.completed",
        "timestamp": 1.5,
        "tool": "logs.search",
        "data": {"result": {"count": 2}},
        "provider": {"sdk": "fake"},
    }
    assert trace.events[0].type == "tool.completed"
    assert trace.events[0].data["tool"] == "logs.search"
    assert trace.events[0].data["data"] == {"result": {"count": 2}}
    assert trace.events[0].data["provider"] == {"sdk": "fake"}


def test_trace_recorder_generates_event_ids(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    trace = TraceRecorder(path, run_id="run-test")

    trace.record("agent.started", timestamp=1.0, agent="SupportCoordinator")
    trace.record("agent.completed", timestamp=2.0, agent="SupportCoordinator")

    payloads = [json.loads(line) for line in path.read_text().splitlines()]
    assert [item["event_id"] for item in payloads] == ["evt-000001", "evt-000002"]


def test_trace_jsonl_loader_loads_canonical_events(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text(
        "\n"
        + json.dumps(
            _event(
                event_type="tool.completed",
                event_id="evt-1",
                timestamp=1.0,
                tool="logs.search",
                data={"result": {"count": 2}},
                provider={"sdk": "fake"},
            )
        )
        + "\n"
    )

    trace = load_trace_jsonl(path)

    assert trace.count("tool.completed", "logs.search") == 1
    event = trace.events[0]
    assert event.type == "tool.completed"
    assert event.timestamp == 1.0
    assert event.data["run_id"] == "run-test"
    assert event.data["event_id"] == "evt-1"
    assert event.data["event_type"] == "tool.completed"
    assert event.data["tool"] == "logs.search"
    assert event.data["data"] == {"result": {"count": 2}}
    assert event.data["provider"] == {"sdk": "fake"}


def test_trace_jsonl_loader_rejects_legacy_type_shape(tmp_path: Path) -> None:
    path = tmp_path / "legacy.jsonl"
    path.write_text(json.dumps({"type": "tool.completed", "timestamp": 1.0, "data": {"tool": "logs.search"}}) + "\n")

    with pytest.raises(TraceFileError, match="Legacy top-level `type`"):
        load_trace_jsonl(path)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{bad\n", "Invalid trace JSON"),
        (json.dumps(["not", "an", "object"]) + "\n", "JSON objects"),
        (json.dumps({"event_id": "evt-1", "event_type": "tool.completed", "timestamp": 1.0}) + "\n", "schema_version"),
        (json.dumps({"schema_version": "1", "event_type": "tool.completed", "timestamp": 1.0}) + "\n", "event_id"),
        (json.dumps({"schema_version": "1", "event_id": "evt-1", "timestamp": 1.0}) + "\n", "event_type"),
        (
            json.dumps(
                {"schema_version": "1", "event_id": "evt-1", "event_type": "tool.completed", "data": {}}
            )
            + "\n",
            "timestamp",
        ),
        (json.dumps(_event(data=[])) + "\n", "`data` must be an object"),
        (json.dumps(_event(provider=[])) + "\n", "`provider` must be an object"),
        (json.dumps(_event(timestamp="soon")) + "\n", "`timestamp` must be a number"),
        (json.dumps(_event(schema_version="2")) + "\n", "Unsupported trace schema_version"),
        (json.dumps(_event(tool=["logs.search"])) + "\n", "`tool` must be a string"),
    ],
)
def test_trace_jsonl_loader_fatal_diagnostics_include_line_numbers(
    tmp_path: Path,
    payload: str,
    message: str,
) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(payload)

    result = load_trace_jsonl_with_diagnostics(path)

    assert not result.ok
    fatal = [item for item in result.diagnostics if item.severity == "fatal"]
    assert fatal
    assert fatal[0].line_number == 1
    assert message in fatal[0].message


def test_trace_jsonl_loader_reports_unknown_event_type_as_warning(tmp_path: Path) -> None:
    path = tmp_path / "future.jsonl"
    path.write_text(json.dumps(_event(event_type="future.provider.event")) + "\n")

    result = load_trace_jsonl_with_diagnostics(path)

    assert result.ok
    assert len(result.trace.events) == 1
    assert [(item.severity, item.code) for item in result.diagnostics] == [("warning", "TRACE015")]


def test_run_monitors_accepts_loaded_canonical_trace(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps(_event(event_type="tool.completed", event_id="evt-1", tool="status_page.draft_update")) + "\n"
        + json.dumps(
            _event(
                event_type="approval.completed",
                event_id="evt-2",
                tool="status_page.draft_update",
                data={"approved": True},
            )
        )
        + "\n"
    )
    trace = load_trace_jsonl(path)

    violations = run_monitors(
        [
            MonitorRule(
                "approval_required",
                "IncidentCommander",
                "high",
                "trace.tool_called(status_page.draft_update)",
                "trace.approval_granted(status_page.draft_update)",
            )
        ],
        trace,
    )

    assert violations == []
