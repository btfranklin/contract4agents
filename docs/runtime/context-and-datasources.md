# Context and Datasources

Every value supplied to an agent has an explicit origin. Contracts define the
portable interface and provenance category; target bindings select the runtime
implementation where one is needed.

## Origin Categories

- `invocation`: a value passed to an entry agent parameter.
- `parent`: a typed value mapped from a delegating parent.
- `handoff`: a typed value carried by a handoff edge.
- `stage`: a previous host-owned run-spec stage output.
- `datasource`: the result of a declared typed resolver.
- `external`: a named value supplied by a target-bound host provider.

There is no generic “the host will somehow provide this” marker.

## Invocation and Edge Inputs

Agent signature parameters are invocation inputs for an entry agent:

```contract
agent IncidentCommander(
    request: IncidentRequest,
    service: ServiceRecord
) -> IncidentBrief:
```

A composition edge explicitly maps every required child input:

```contract
composition investigate from IncidentCommander to LogInvestigator:
    mode = delegate
    description = "Investigate when log evidence is needed."
    history = none
    map request = input.request
    map service = input.service
```

Semantic analysis rejects missing target-input mappings.

## Datasource Interfaces

A datasource is a portable typed resolver, not an implementation path:

```contract
datasource incident.timeline(
    incident: IncidentRecord
) -> IncidentTimeline:
    description = "Resolve the current incident timeline."
    render = markdown
    cache = run
```

An agent maps each resolver input from its typed invocation or an earlier
context slot:

```contract
context incident: IncidentRecord from external current_incident
context timeline: IncidentTimeline from datasource incident.timeline:
    map incident = context.incident
```

Mappings are part of canonical IR and are type-checked. The runtime does not
ask the model or host to reconstruct resolver arguments implicitly.

The target binding supplies the implementation:

```toml
[targets.openai.datasources."incident.timeline"]
python = "incident_app.context:resolve_timeline"
```

The contract can change targets without changing the datasource interface.
Planning validates binding coverage and records the selected implementation
identity without executing it.

## External Context

External context names a host-owned value and its portable handling metadata:

```contract
external_context current_incident -> IncidentRecord:
    description = "The incident selected by the authenticated host session."
    sensitivity = confidential
    render = markdown
```

An agent declares exactly how it receives that value:

```contract
context incident: IncidentRecord from external current_incident
```

The target binding supplies the provider:

```toml
[targets.openai.external_context.current_incident]
python = "incident_app.context:current_incident"
```

The materialization plan retains the context semantic ID, provider locator,
type, sensitivity, rendering, and host obligation.

## Rendering and Sensitivity

Portable render modes are `markdown`, `json`, and `text`. Sensitivity values
are `public`, `internal`, `confidential`, and `restricted`.

Structured host values should remain structured for validation, tools, and
application logic until an audience-safe renderer creates model-visible text.
Secrets, provider tokens, forbidden internal identifiers, and unbounded blobs
must not be copied into model instructions or general trace payloads.

Trace provenance records the provider and safe references to supplied values.
Audience redaction rules determine which trace consumers may see sensitive
fields.

## Caching

Datasource cache modes are portable runtime expectations:

- `none`: resolve every time.
- `run`: reuse within one normalized run.
- `thread`: reuse only through explicit thread-scoped state supplied by the
  runtime provider.

The plan reports how the selected target implements the requested cache mode.
A required unsupported semantic fails closed.

## Materialization and Resolution

During planning and materialization Contract4Agents:

1. verifies that every referenced datasource and external context exists;
2. verifies input and output types;
3. requires a compatible target binding;
4. safely inspects callable shape when the binding supports it;
5. records the provider, provenance, rendering, caching, and sensitivity in the
   immutable plan;
6. wires the implementation into the native graph;
7. emits materialization evidence with stable semantic IDs.

During execution, the runtime or host resolves declared values, validates the
result shape, applies rendering and redaction, records cache behavior, and emits
normalized context/datasource events. Resolution never scans arbitrary installed
modules or invents an undeclared provider.

The materialized graph exposes this runtime directly:

```python
from contract4agents.tracing import RecordingNormalizedTraceSink

trace_sink = RecordingNormalizedTraceSink()
system = materialize(
    "agent_contracts",
    target="openai",
    profile="production",
    normalized_trace_sink=trace_sink,
)
context = await system.context.resolve_agent(
    "SupportAgent",
    {"request": request},
    run_id=run_id,
    thread_id=thread_id,
)
```

`NormalizedTraceSink` is shared by context resolution, provider response
normalization, and trace processors. Use `AtomicTraceFileSink` when normalized
events need crash-safe single-process JSONL persistence. The host still owns
workflow-state transactions, durable recovery policy, and multi-process
coordination.

The result contains typed values plus audience-safe rendered forms. Run and
thread cache scopes are enforced by the resolver, and every resolution emits a
normalized event carrying the contract, plan, agent, context, and datasource
identities without copying the resolved value into generic trace data.

## Failures

Missing providers, type mismatches, ambiguous origins, binding import failures,
unsupported rendering/caching guarantees, and sensitive-value exposure are
failures or explicit plan caveats according to requiredness. They are never
silently treated as resolved context.
