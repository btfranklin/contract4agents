# OpenAI Target Reference

The OpenAI target materializes canonical Contract4Agents IR into ordinary
OpenAI Agents SDK objects. It is contract-first and plan-first: users review a
provider-neutral materialization plan before the same mapping constructs the
native graph.

Install the optional target dependencies:

```bash
pdm add "contract4agents[openai]"
```

## Target Bindings

`contract4agents.targets.toml` contains target-specific locators and options:

```toml
schema_version = "2"

[targets.openai]
adapter = "openai"

[targets.openai.tools."documents.search"]
python = "my_app.tools:search_documents"

[targets.openai.tools."web.search"]
provider = "web_search"
search_context_size = "medium"

[targets.openai.datasources."account.history"]
python = "my_app.context:account_history"

[targets.openai.external_context.authenticated_account]
python = "my_app.context:authenticated_account"

[targets.openai.environments.in_process]
provider = "contract4agents.runtime:InProcessEnvironment"

[targets.openai.profiles.test]
default_model = "test-model"

[targets.openai.profiles.production]
default_model = "gpt-5.2"

[targets.openai.profiles.production.agents.ResearchAgent]
model = "gpt-5.6-luna"
```

Every target has at least one named profile. Profiles are complete and do not
inherit: `default_model` and explicit agent overrides must resolve a model for
every canonical agent, and an override naming an unknown agent is invalid.
Bindings cannot contain permissions, prompts, controls, schemas, agent
factories, output-type mappings, or composition registries.

Profiles own model identifiers and provider options. Environment variables own
credentials and may select a target and profile; target-binding files never
interpolate environment variables. Programmatic bindings remain useful for
tests and control planes, but the resulting named plan must be persisted as the
auditable configuration used for the run.

Python locators use `module:attribute`. Planning may import a locator to inspect
its callable signature, but it never calls application code.

## Plan Without Constructing Agents

```bash
contract4agents plan agent_contracts --target openai --profile production \
  --out .contract/build/openai-production-plan.json
```

The plan contains the contract digest, resolved model for every agent, binding
identities, grants and authorization, native composition mappings, control and
isolation mechanisms, expected telemetry, host obligations, and target caveats.
It contains no live SDK objects or process-specific callable addresses.

Each mapping reports one outcome:

- `exact`: the SDK natively implements the requested semantic.
- `host_enforced`: a named host or environment provider enforces it.
- `emulated`: generated runtime behavior preserves it.
- `degraded`: construction would lose a documented semantic.
- `unsupported`: no honest implementation exists.

Required degraded and unsupported mappings fail planning.

## Materialize the Complete Graph

```python
from contract4agents import materialize

system = materialize(
    "agent_contracts",
    target="openai",
    profile="production",
)

lead = system.agents["ResearchLead"]
plan = system.plan
```

`system.agents` is keyed by the names written in contracts. Its values are
native OpenAI Agents SDK `Agent` objects. `system.graph` also exposes generated
output types, resolved implementations, native grant and composition objects,
the typed context runtime, environment-enforcement evidence, and
graph-validation evidence.

Construction uses two passes so source-file order does not constrain the graph:

1. Build agent shells, instructions, generated Pydantic output types, tools,
   approval configuration, and hooks.
2. Resolve named delegation and handoff edges across the complete graph.

The adapter then validates models, tools, grants, approvals, outputs,
composition, and hooks against the immutable plan.

## Tools and Approvals

A contract tool is a shared portable interface. Its grant selects `host`,
`provider_hosted`, `remote`, or a named environment execution boundary. The
OpenAI target currently resolves Python host callables and supported
provider-native tools from target bindings.

Raw Python callables are converted to native function tools. Dotted contract
names are mapped to reversible SDK-safe names. An `approval_required` grant
sets the native tool's approval requirement and creates expected approval
telemetry. The application remains responsible for approval decisions and UI.

Missing implementations, signature mismatches, unverified required approval
enforcement, and unsupported remote bindings fail closed.

## Delegation and Handoff

For a named `composition` edge:

- `delegate` creates an agent-as-tool that returns the target agent's typed
  final output to the source agent.
- `handoff` creates a native SDK handoff that transfers control.

Descriptions, typed input mappings, history transfer, and isolation profiles
come from the edge. OpenAI agent-as-tool and handoff payloads are model supplied,
so the plan classifies source-value transfer as emulated and exposes a host
obligation when equality with the declared source mapping must be proven. The
host does not supply agent factories, agent-tool registries, or ordinary
handoff registries.

Provider-specific callback behavior that cannot be expressed portably belongs
in an explicitly nonportable target binding, never in portable source.

## Isolation

The built-in `InProcessEnvironment` can report and enforce only the dimensions
it actually controls, such as fresh context, capability allowlisting, fresh
state, and return-channel restriction. It does not claim an operating-system,
filesystem, network, process, or secret boundary.

A contract requiring stronger isolation must select an environment provider
that implements `EnvironmentProvider` and supplies enforcement evidence. If the
selected provider cannot satisfy a required dimension, materialization stops.

## Running

Run the returned object with the normal SDK API:

```python
from agents import Runner

result = await Runner.run(system.agents["ResearchLead"], input=user_request)
```

If the entry agent declares datasource or external context, resolve it through
`system.context.resolve_agent(...)` first and pass the returned rendered values
through the application's normal SDK context or input strategy. Resolution
validates contract types, enforces declared cache scopes, and emits normalized
provenance events. Contract4Agents deliberately does not hide the remaining
provider-specific injection step: the OpenAI SDK has no framework-native typed
entry-input channel.

Contract4Agents does not replace the SDK runner, provider trace backend, session
store, or deployment environment. It constructs and validates the graph those
systems execute.

## Materialization Evidence

Pass a `TraceSink` to `materialize` to capture deterministic construction
evidence. `RecordingTraceSink` is useful for tests and small host integrations:

```python
from contract4agents.materialization import RecordingTraceSink

sink = RecordingTraceSink()
system = materialize(
    "agent_contracts",
    target="openai",
    profile="production",
    trace_sink=sink,
)
```

Events identify the contract and plan digests and the stable semantic IDs for
constructed agents, tools, grants, approvals, composition edges, output types,
context, datasources, and isolation mechanisms. Runtime provider spans should
be correlated into the normalized trace schema.

Use the supplied Agents SDK tracing processor for runtime correlation:

```python
from agents import add_trace_processor
from contract4agents.tracing import OpenAINormalizedTraceRouter, TraceAttempt

router = OpenAINormalizedTraceRouter()
add_trace_processor(router)  # once at process startup

session = router.open_session(
    artifacts.ir,
    system.plan,
    run_id=run_id,
    thread_id=thread_id,
)
attempt = TraceAttempt("planner:1", "planner:attempt:1", 1)
with session:
    with session.bind_attempt(attempt, agent="Planner"):
        result = await Runner.run(agent, input=prompt)
        session.record_result(result, agent="Planner", attempt=attempt)

trace = session.normalized_trace()
trace_closure = session.closure_evidence
```

The router and session map native agent, function-tool, delegation, and handoff spans
to stable contract IDs, add output-validation evidence for successful agent
spans, and preserves provider trace/span correlation. It intentionally does not
copy raw provider inputs or outputs into normalized payloads.

For a retried host invocation, bind portable attempt identity around each
runner call. The binding annotates evidence but does not catch, retry, or select
an attempt:

```python
attempt = TraceAttempt("planner:1", "planner:attempt:1", 1)
with session.bind_attempt(attempt, agent="Planner"):
    result = await Runner.run(planner, input=prompt)

session.normalize_response_events(
    result.raw_responses,
    agent="Planner",
    attempt=attempt,
)
```

If the runner raises, call
`session.normalize_exception_responses(exception, agent=..., attempt=...)`
before retrying or reraising so provider-hosted call evidence preserved in
`exception.run_data.raw_responses` is not lost. This helper is deliberately
duck-typed and does not classify a general Agents SDK exception as an output
schema failure.

The host may record its narrower validation and terminal-selection decisions
with `record_output_schema_failure(...)` and `record_terminal_attempt(...)`.
Output controls assess the explicitly selected attempt for each invocation;
earlier failed attempts remain auditable. Contract4Agents does not decide when
an attempt is terminal or whether a retry is allowed.

Every successful runner result must close its response path through
`record_result(...)` or `normalize_response_events(...)`, even when no hosted
tool was expected:

```python
session.normalize_response_events(
    result.raw_responses,
    agent="CurrentTruthScout",
    attempt=attempt,
)
trace = session.normalized_trace()
```

OpenAI provider-hosted call items are matched fail-closed against the reviewed
plan. The currently materialized `web_search_call` must match exactly one
enabled provider-hosted grant for the agent plus the `openai:web_search`
locator. A successful match emits canonical `tool.completed` evidence; zero or
multiple matches, other recognized hosted calls, and unknown call-like items
emit `capability.undeclared`, which trace conformance rejects before assurance
or eval scoring. Provider response/request/call correlation and model metadata
are preserved when available, while provider prompts, actions, and results
remain outside the normalized payload.

Every inspected response emits a normalization receipt, and every supplied
response iterable emits a batch receipt, including a zero-response or zero-call
batch. These receipts let the session distinguish an inspected empty path from
one the host never submitted. Closing the session produces identity-bound
`TraceClosureEvidence`; incomplete SDK traces or missing success/exception
response paths keep closure incomplete or unverified.

For a durable recovery point without closing the active session, capture an
internally consistent pair after at least one normalized event exists:

```python
checkpoint = session.checkpoint()
# Persist checkpoint.trace and checkpoint.closure through the host's durable
# recovery mechanism before advancing application workflow state.
```

The v2 closure frontier binds the exact ordered event count and digest. Resume
only from the matching pair:

```python
session = router.open_session(
    artifacts.ir,
    system.plan,
    run_id=run_id,
    thread_id=thread_id,
    prior_trace=loaded_trace,
    prior_closure=loaded_closure,
)
```

Prior attempts cannot be rebound around another `Runner.run`; a recovered SDK
retry needs a new `TraceAttempt` with the next number and exact `retry_of`
identity. Host-semantic reconciliation may still record terminal selection or
output-schema failure evidence against a sealed prior attempt. Channel closure
for a resumed run is conservative across every SDK-execution segment. A
checkpoint does not make trace, closure, and application state one transaction;
the host owns persistence ordering, crash policy, and workflow recovery.

Supported hosted-call status is preserved: completed or succeeded calls emit
`tool.completed`, failed, cancelled, or incomplete calls emit `tool.failed`,
and other nonterminal statuses emit `tool.started`. Hosted MCP discovery items
such as `mcp_list_tools` are recognized as unsupported evidence rather than
silently discarded.

Function, custom-tool, computer, shell, and patch calls are dispatched by the
host or SDK and therefore remain on their existing span or host-evidence paths.
Messages, reasoning, and other non-call output items are intentionally ignored.

The Agents SDK processor registry is process-global and has no individual
removal API. Do not register a router per run and do not use
`set_trace_processors()` to replace other integrations in a long-lived
service. Register one router at startup and create disposable sessions; ended
provider traces are removed from the router. Session close also removes every
remaining router binding, including a provider trace that never delivered
`on_trace_end`, while leaving its lifecycle closure incomplete. Completed or
abandoned closed sessions are therefore not retained by the router.

## Offline and Live Validation

The default test suite constructs real SDK objects with deterministic local
models and no credentials. Live provider checks are opt-in:

```bash
CONTRACT4AGENTS_RUN_OPENAI_LIVE=1 pdm run test:openai-live
```

The live suite exercises contract compilation, production-profile planning,
native graph materialization, typed context resolution, agent-as-tool
delegations, structured output, SDK-span correlation, and hosted web-search
response normalization.

See [Validation and Quality Gates](../quality/validation.md) before interpreting
a skipped live check as coverage.
