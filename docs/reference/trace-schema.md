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

For the OpenAI Agents SDK, `OpenAINormalizedTraceRouter` implements the global
trace-processor callbacks. Register exactly one router for the process, then
open a disposable `OpenAINormalizedTraceSession` for each logical run. The
router binds provider trace IDs to the context-local session active when the
SDK trace starts and releases that binding when the trace ends or the owning
session closes. Closing a session whose SDK trace never ended preserves
incomplete lifecycle evidence while purging the router binding, so a closed
session is not retained by the global SDK registry. Its
`normalized_trace()` result retains provider IDs while excluding raw provider
inputs and outputs.

## Attempts and retries

The host still owns retries. Contract4Agents supplies `TraceAttempt` so every
event from one attempt can carry validated `data.attempt` identity:

```python
from contract4agents.tracing import TraceAttempt

attempt = TraceAttempt(
    invocation_id="research-section:1",
    attempt_id="research-section:1:attempt:2",
    number=2,
    retry_of="research-section:1:attempt:1",
)

with session.bind_attempt(attempt, agent="SectionResearcher"):
    result = await Runner.run(agent, input=prompt)
```

The session uses context-local binding and remembers the attempt active when
each span starts, including when the span ends after the binding scope exits.
Response normalization also accepts an explicit `attempt=` argument.

After the host has decided that no further retry will replace an attempt, it
records that decision explicitly:

```python
session.record_terminal_attempt(
    agent="SectionResearcher",
    attempt=attempt,
    outcome="succeeded",
)
```

One `attempt.selected` event is allowed per invocation. Retry chains must be
complete, ordered, and confined to that invocation, and a selected attempt must
have other observed execution evidence. Output-conformance assessment uses only
the selected attempt for each invocation. Earlier failed attempts remain in the
trace; they do not silently disappear or permanently poison a later accepted
terminal output. A selected `output.schema_failed` violates output conformance,
while a general selected attempt failure without schema evidence is
`unverified`. A separate operational control may impose a stricter policy such
as allowing no failed attempts.

Provider-hosted tools are also visible in Agents SDK model responses. Normalize
those response items after each run:

```python
events = session.normalize_response_events(
    result.raw_responses,
    agent="CurrentTruthScout",
    attempt=attempt,
)
```

Agents SDK exceptions may retain model responses on
`exception.run_data.raw_responses`. Normalize them before the host retries or
reraises:

```python
events = session.normalize_exception_responses(
    exception,
    agent="CurrentTruthScout",
    attempt=attempt,
)
```

`normalize_openai_exception_responses(...)` is the standalone equivalent. It
does not catch exceptions, decide retries, emit a generic agent failure, or
infer that an SDK exception was a schema failure. Host-side canonical output
validation can record the narrower fact through
`session.record_output_schema_failure(...)`.

### Snapshots and recovery

After the session has at least one normalized event, `session.snapshot()`
returns a `TraceCaptureSnapshot` containing an
immutable normalized trace and its closure evidence captured under the same
session lock. The closure manifest v1 frontier records the exact event count
and canonical SHA-256 digest of that ordered trace. A later event advances the
frontier, so an older closure cannot be applied to the newer trace.

Persist the snapshot's `trace` and `closure` together as one recovery unit.
To continue the same logical run in another session or process, supply that
exact pair as `prior_trace=` and `prior_closure=` to `router.open_session(...)`.
The session validates run, thread, contract, plan, frontier, attempt identities,
and retry chains before accepting new evidence. Prior attempts are sealed: a
new SDK execution uses a new attempt ID and number with `retry_of` pointing to
the prior attempt. Host-semantic reconciliation evidence, including terminal
selection or an output-schema failure discovered after recovery, may still
reference a sealed attempt with its original agent identity.

Closure and terminal selection remain different claims. A snapshot may have
complete instrumentation closure before the host selects the terminal attempt;
output assurance remains unverified until the host supplies that semantic
selection. Contract4Agents does not coordinate application state, trace files,
and closure files as a transaction, nor does it decide whether recovery or a
retry is allowed.

`normalize_openai_response_events(...)` is the corresponding standalone API.
For recognized provider-hosted call items it resolves exactly one enabled
`provider_hosted` grant whose plan locator matches the agent, provider, and
tool. The currently materialized OpenAI tool is `web_search_call`, matched to
`provider = "openai"` and `tool = "web_search"`. It emits `tool.completed`
evidence with canonical capability/grant IDs. Missing or ambiguous matches,
recognized hosted calls that the adapter cannot materialize, and unknown
call-like response items instead emit `capability.undeclared`; they are never
silently assigned to a capability or discarded. Every inspected response emits
`provider.response.normalized`, and every supplied response iterable emits
`provider.response_batch.normalized`, including a legitimate zero-response or
zero-call batch. These receipts distinguish inspection from omission.

For a supported hosted call, provider status selects `tool.started`,
`tool.completed`, or `tool.failed`; an observed failed call is never rewritten
as a completion. Hosted MCP discovery items such as `mcp_list_tools` are
recognized even though their names do not end in `_call`.

Function, custom-tool, computer, shell, and patch calls are host-dispatched,
not provider-hosted evidence. Response normalization leaves those items to SDK
spans or host instrumentation. Messages, reasoning, and other non-call output
items are intentionally ignored.
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

A normal return from `NormalizedTraceSink.emit()` acknowledges one accepted
event. If it raises, trace sessions propagate the exception without advancing
their accepted event frontier. Multi-event normalization is prefix-atomic: all
events acknowledged before the failure remain accepted, while the failed event
and the remaining suffix are not committed by the session. Retry, recovery,
and workflow-state policy remain host responsibilities.

Before assurance or eval scoring, Contract4Agents automatically calls
`validate_trace_conformance(ir, plan, trace)`. It rejects digest mismatches,
explicit undeclared-capability evidence, tool events without complete semantic
identity, and unknown, disabled, or mismatched grants through structured
`TraceConformanceError.issues`.

## Trace Evidence

```python
from contract4agents.tracing import assess_trace_evidence

result = assess_trace_evidence(
    trace,
    plan.expected_event_types,
    closure=trace_closure,
)
```

Event-family occurrence is a diagnostic; it is not proof that every execution
path was instrumented. `TraceClosureEvidence` binds one run, contract, plan,
invocation attempt, provider trace, and response-normalization path. It records
which channels—such as `agent`, `tool`, `approval`, `output`, and
`provider_response`—are closed. Missing, inconsistent, or incomplete closure
keeps absence and upper-bound claims `unverified`. Directly observed positive
evidence can still prove a positive claim.

`TraceClosureManifest` is the versioned JSON artifact used by the CLI and
assurance bundles. Version 1 binds closure to an exact ordered event frontier;
other manifest versions are rejected rather than treated as negative assurance.
Complete closure must cover exactly every attempt observed in its run. The
OpenAI session produces closure for SDK lifecycle and response paths; the host
uses `attest_channels(...)` for adjacent instrumentation that the session cannot
observe. Contract4Agents validates these identities but cannot prove that a
dishonest host disclosed work it deliberately omitted.

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
