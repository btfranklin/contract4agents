# Trace Schema Reference

Contract4Agents trace files are canonical JSONL files. Each line is one JSON
object using trace schema version `1`.

```json
{
  "schema_version": "1",
  "run_id": "run-123",
  "event_id": "evt-001",
  "event_type": "tool.completed",
  "timestamp": 1.0,
  "agent": "SupportCoordinator",
  "tool": "crm.create_note",
  "data": {},
  "provider": {}
}
```

## Envelope Fields

Required fields:

- `schema_version`: currently `1`.
- `event_id`: stable event identifier within the trace.
- `event_type`: normalized event name.
- `timestamp`: numeric timestamp.

Optional index fields:

- `run_id`: run identifier shared by events from the same run.
- `agent`: agent name when the event belongs to an agent.
- `tool`: host or provider tool name when the event belongs to a tool call.
- `datasource`: datasource name or type when the event belongs to datasource resolution.
- `stage`: stage or checkpoint name.
- `guardrail`: guardrail name.
- `assertion`: assertion name or expression.

Payload fields:

- `data`: event-specific object. Use this for arguments, results, approval
  decisions, produced type names, failure reasons, and other normalized payload.
- `provider`: provider-specific object. Use this for SDK run IDs, model names,
  token counts, latency, and other adapter metadata.

`TraceRecorder` writes `data` and `provider` as objects on every event. The
loader rejects non-object `data` or `provider` values. Legacy JSONL with a
top-level `type` field is invalid; use `event_type`.

## Known V1 Events

Agent events:

- `agent.started`
- `agent.completed`
- `agent.handoff`

Host tool events:

- `tool.requested`
- `tool.started`
- `tool.allowed`
- `tool.denied`
- `tool.completed`
- `tool.failed`
- `host_tool.requested`
- `host_tool.started`
- `host_tool.completed`
- `host_tool.failed`

Hosted provider tool events:

- `hosted_tool.requested`
- `hosted_tool.started`
- `hosted_tool.completed`
- `hosted_tool.failed`

Datasource events:

- `datasource.started`
- `datasource.resolved`
- `datasource.failed`

Approval events:

- `approval.requested`
- `approval.completed`

Run review events:

- `stage.completed`
- `output.accepted`
- `output.rejected`
- `output.schema_failed`
- `assertion.evaluated`
- `guardrail.rejected`

Model events:

- `llm.started`
- `llm.completed`

Unknown `event_type` values are warnings in diagnostic loading, not fatal
errors. This lets future provider events be inspected while still rejecting
malformed trace envelopes.

## Loading

Host code can load trace files directly:

```python
from pathlib import Path

from contract4agents.runtime import load_trace_jsonl, load_trace_jsonl_with_diagnostics

trace = load_trace_jsonl(Path("run.trace.jsonl"))
diagnostic_result = load_trace_jsonl_with_diagnostics(Path("run.trace.jsonl"))
```

`load_trace_jsonl(...)` raises `TraceFileError` for any fatal diagnostic.
`load_trace_jsonl_with_diagnostics(...)` returns a `TraceLoadResult` with the
loaded in-memory trace and structured `TraceDiagnostic` records.

Fatal diagnostics include invalid JSON, non-object JSONL lines, missing
`schema_version`, `event_id`, `event_type`, or `timestamp`, unsupported schema
versions, non-object `data` or `provider`, bad timestamps, and legacy top-level
`type`.
