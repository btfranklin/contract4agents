from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract4agents.runtime._trace_io import TraceFileError, load_trace_jsonl


def test_trace_jsonl_loader_accepts_trace_events(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text(
        "\n"
        + json.dumps({"type": "tool.completed", "timestamp": 1.0, "data": {"tool": "logs.search"}})
        + "\n"
    )

    trace = load_trace_jsonl(path)

    assert trace.count("tool.completed", "logs.search") == 1


def test_trace_jsonl_loader_reports_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("{bad\n")

    with pytest.raises(TraceFileError, match="Invalid trace JSON"):
        load_trace_jsonl(path)


def test_trace_jsonl_loader_reports_invalid_data_shape(tmp_path: Path) -> None:
    path = tmp_path / "bad-data.jsonl"
    path.write_text(json.dumps({"type": "run.started", "data": []}) + "\n")

    with pytest.raises(TraceFileError, match="data must be an object"):
        load_trace_jsonl(path)
