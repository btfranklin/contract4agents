# Trace Schema Reference

Contract4Agents normalized trace schema version `1` connects observed execution
to the exact contract and materialization plan that governed it. JSONL is the
portable storage form; provider-native spans remain available through
correlation references.

## Event Envelope

```json
{
  "schema_version": "1",
  "run_id": "run-123",
  "thread_id": "thread-1",
  "event_id": "evt-000004",
  "parent_event_id": "evt-000003",
  "event_type": "approval.completed",
  "timestamp": 1784098974.25,
  "contract_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "plan_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "semantic": {
    "agent_id": "agent:IncidentCommander",
    "capability_id": "tool:status.publish",
    "composition_id": null,
    "context_id": null,
    "grant_id": "grant:IncidentCommander:status.publish",
    "isolation_id": null,
    "quality_id": null,
    "control_ids": ["control:IncidentCommander:approval:status.publish"]
  },
  "data": {"approved": true},
  "provider": {
    "name": "openai",
    "run_id": "provider-run-456",
    "span_id": "provider-span-789"
  },
  "evidence_refs": ["provider:openai:provider-span-789"],
  "provenance": {"source": "approval_callback"},
  "redaction": {"state": "safe", "applied": [], "rules": []}
}
```

## Run Identity

Every event repeats these immutable run fields:

- `run_id`: normalized execution identity.
- `thread_id`: conversation or workflow thread identity.
- `contract_digest`: SHA-256 identity of canonical IR.
- `plan_digest`: SHA-256 identity of the reviewed materialization plan.

A loaded trace represents one run and one thread under one contract and plan.
Mixing identities is invalid.

## Event Identity and Causality

- `event_id` is unique within the trace.
- `parent_event_id` is either `null` or the ID of another event in the run.
- `event_type` is a normalized dotted name such as `tool.completed`.
- `timestamp` is a finite numeric timestamp.

The loader rejects duplicate IDs, missing parents, parent cycles, cross-run
parent references, malformed IDs, and non-finite timestamps.

## Stable Semantic References

`semantic` connects an event to canonical IDs:

- `agent_id`: `agent:<name>`
- `capability_id`: `tool:<name>` or `datasource:<name>`
- `composition_id`: `edge:<name>`
- `context_id`: `context:<agent>:<slot>`
- `grant_id`: `grant:<agent>:<capability>`
- `isolation_id`: `isolation:<name>`
- `quality_id`: `quality:<agent>:<rubric>`
- `control_ids`: zero or more `control:<...>` IDs

These references let evals, assessments, diffs, and assurance joins survive source
file reordering and display-name ambiguity.

## Provider Correlation

`provider.name` is required. Optional `run_id`, `trace_id`, `span_id`, and
`request_id` preserve links to provider-native evidence. `evidence_refs` may
also identify raw spans, host attestations, artifacts, or other immutable
evidence that the normalized payload does not duplicate.

Normalization is not intended to replace a provider's full trace representation
or an existing observability backend.

For the OpenAI Agents SDK, `OpenAINormalizedTraceProcessor` implements the SDK
tracing-processor callbacks and correlates native agent, tool, delegation, and
handoff spans. Register one processor per logical run with
`agents.add_trace_processor(...)`; its `normalized_trace()` result retains the
provider IDs while excluding raw provider inputs and outputs.

Provider-hosted tools are also visible in Agents SDK model responses. Normalize
those response items after each run:

```python
events = processor.normalize_response_events(
    result.raw_responses,
    agent="CurrentTruthScout",
)
```

`normalize_openai_response_events(...)` is the corresponding standalone API.
For `web_search_call` items it resolves exactly one enabled `provider_hosted`
grant whose plan locator matches the agent, `provider = "openai"`, and
`tool = "web_search"`. It emits `tool.completed` evidence with canonical
capability/grant IDs. Missing or ambiguous matches instead emit
`capability.undeclared`; they are never silently assigned to a capability.
Only provider status, model metadata when exposed by the SDK, response/request
correlation, and call IDs are retained. Queries, actions, prompts, and results
are not copied into normalized events.

## Provenance and Redaction

`provenance` records where the normalized evidence came from. `redaction`
records whether the event is safe, sensitive, or already redacted and may carry
JSON Pointer rules declaring which audiences can see particular values.

Redactable roots are `data`, `provider`, `evidence_refs`, and `provenance`.
Audience values are `model`, `adapter`, `host`, `evaluator`, and `reviewer`.
Redaction is applied before export. Hidden controls, thresholds, secrets, and
sensitive context must not be copied into broadly visible trace views.

## Reading and Writing JSONL

```python
from pathlib import Path

from contract4agents.tracing import load_trace_jsonl, write_trace_jsonl

trace = load_trace_jsonl(Path("run.trace.jsonl"))
write_trace_jsonl(Path("normalized.trace.jsonl"), trace, audience="reviewer")
```

Use `dumps_trace_jsonl` and `loads_trace_jsonl` for in-memory data. Loading is
strict: invalid JSON, unsupported schema versions, incomplete envelopes,
unknown object fields, broken identity, and malformed semantic references fail.
`write_trace_jsonl` writes a same-directory temporary file, flushes and syncs
it, then atomically replaces the destination.

For incremental single-process persistence, use an atomic normalized sink:

```python
from pathlib import Path

from contract4agents.tracing import AtomicTraceFileSink, TraceRunContext

context = TraceRunContext(run_id, thread_id, contract_digest, plan_digest)
sink = AtomicTraceFileSink(Path("run.trace.jsonl"), context, append=True)
```

Resume validates the complete existing file and its exact run context. Every
emission validates and atomically writes the complete candidate trace before
advancing in-memory state. `RecordingNormalizedTraceSink` and
`NoOpNormalizedTraceSink` provide in-memory and discard behavior. These sinks
do not coordinate multiple processes or transact trace evidence with host
workflow state; those remain host responsibilities.

Before assurance or eval scoring, Contract4Agents automatically calls
`validate_trace_conformance(ir, plan, trace)`. It rejects digest mismatches,
explicit undeclared-capability evidence, tool events without complete semantic
identity, and unknown, disabled, or mismatched grants through structured
`TraceConformanceError.issues`.

## Trace Completeness

```python
from contract4agents.tracing import assess_trace_completeness

result = assess_trace_completeness(trace, plan.expected_telemetry)
```

Completeness is evaluated against the plan, not against a generic event list.
Missing required event families produce an `unverified` result with exact
reasons. This matters especially for negative claims: the absence of a tool
event does not prove the tool was not called unless complete tool telemetry was
expected and present for the run.

## OpenTelemetry Export

`export_open_telemetry` maps normalized events to spans through a small
structural tracer protocol. The integration has no hard OpenTelemetry package
dependency; pass a real compatible tracer from the host application.

```python
from contract4agents.tracing import export_open_telemetry

export_open_telemetry(trace, tracer, audience="reviewer")
```

Audience redaction happens before span attributes are emitted. Provider-native
correlation IDs and contract/plan identities remain available for joining the
export with reviewed artifacts.
