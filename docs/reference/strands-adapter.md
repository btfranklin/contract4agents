# Strands Target Reference

The Strands target constructs native `strands.Agent` and `AgentTool` objects
from your contracts. Your application invokes the returned agents and handles
sessions and approval decisions.

Start with [Target Bindings](#target-bindings), check
[Semantic Support](#semantic-support), then use
[Materialize and Run](#materialize-and-run). The trace sections explain the
additional setup for assessment and recorded evidence.

Install the optional target dependency:

```bash
pdm add "contract4agents[strands]"
```

Without a model factory, `check`, `plan`, and `visualize` do not import Strands
and work without this extra. A configured factory module can import its provider
SDK and needs those dependencies for binding checks. Default materialization
reports the required extra when the SDK is not installed.

## Target Bindings

Strands currently accepts Python implementations for portable tools,
datasources, and external context:

```toml
schema_version = "1"

[targets.strands]
adapter = "strands"

[targets.strands.tools."records.lookup"]
python = "my_app.tools:lookup"

[targets.strands.datasources."records.current"]
python = "my_app.context:current_record"

[targets.strands.external_context.request_context]
python = "my_app.context:request_context"

[targets.strands.profiles.production]
default_model = "us.amazon.nova-pro-v1:0"
```

Python locators use `module:callable`. Checking and planning import the callable
to inspect its signature but never invoke it. Remote, TypeScript, module, MCP,
and provider-hosted tool locators are unsupported. Bind search as an ordinary
Python tool; the adapter does not silently substitute a Strands or provider
search package.

The default model path constructs
`strands.models.bedrock.BedrockModel(model_id=..., **options)`. Credentials stay
in the host environment rather than target bindings.

For another Strands model provider, configure a model factory:

```toml
[targets.strands.profiles.production.options]
model_factory = "my_app.models:make_model"
temperature = 0.2
```

```python
from strands.models import Model


def make_model(*, model: str, options: dict[str, object]) -> Model:
    ...
```

Profile options are merged with agent overrides. Checking and planning inspect
but do not call the factory. Materialization calls it once per agent with the
selected model and remaining options; `model_factory` and `environment` are
excluded. The return value must implement `strands.models.Model`.
Nested options reach `BedrockModel` or the configured model factory as ordinary
dictionaries and lists.

## Native Schema Output

`output_mode` accepts the `StrandsOutputMode` values `tool` and `schema`.
The default is `tool`. Its existing two-argument model factories and native
structured-output tool behavior stay supported.

Schema mode requires a model factory. It receives a typed generated output
context in addition to the model and options:

```toml
[targets.strands.profiles.production.options]
output_mode = "schema"
model_factory = "my_app.models:make_model"
```

```python
from collections.abc import Mapping
from strands.models import Model
from contract4agents.adapters.strands import StrandsOutputContext
from contract4agents.adapters.strands_openai import ReasoningOpenAIModel


def make_model(*, model: str, options: Mapping[str, object], output: StrandsOutputContext) -> Model:
    return ReasoningOpenAIModel(
        model_id=model,
        bedrock_mantle_config={"endpoint": "bedrock-runtime", "region": "us-east-1"},
        client_args={"max_retries": 0},
        params={"response_format": output.chat_completions_format()},
    )
```

`output.schema` is the full generated contract schema.
`output.chat_completions_format()` returns a closed, strict JSON Schema format
for the Bedrock Chat Completions API. It projects unsupported generation bounds;
the original Pydantic type retains them. Strict generation requires every
property. An optional property must declare a default that passes its original
schema before projection. An empty list default is valid only when the original
array permits zero items. Host validation applies the declared defaults and
keeps the original constraints. The raw response remains unchanged in the
content record. The factory must configure this native
format. Materialization reads the model's public configuration and refuses a
missing or different strict schema. This proves configuration, not that a live
provider accepts the schema or constrains decoding.

The returned agent uses the normal Strands loop and its granted Python tools.
It has no synthetic result tool or SDK forced-output retry. Extract its final
response through the public helper:

```python
from contract4agents.adapters.strands import extract_strands_output

result = await agent.invoke_async(user_request)
output = extract_strands_output(system, agent, result)
```

Extraction requires a complete assistant response with one JSON value. It
rejects duplicate members, non-finite numbers, unsupported result content, and
contract violations. The normalized trace uses the same validator. A rejected
native result cannot produce `output.accepted`.

The [runnable native schema example](../../examples/strands-schema/README.md)
contains a complete target and factory. It selects Kimi through the verified
Strands 1.64 provider. Model IDs, effort, token bounds, deadlines and fallback
remain application policy. A shared agent has mutable conversation state;
materialize independent graphs for concurrent jobs. Use normal Strands message
history for continued conversations and retain the actual assistant/tool turns.

### Reasoning and Provider Evidence

`ReasoningOpenAIModel` extends the public Strands `OpenAIModel` serialization
method. It preserves assistant reasoning as `reasoning_content`, with the
original tool-call identities and tool results. It refuses reasoning that cannot
be represented as text. It does not replace the SDK tool loop or patch requests.
Use it only with a provider that supports this reasoning field.

Attach a host-owned `StrandsModelObserver` with `model.observe(observer)`.
Its `request` and `event` methods receive copied `dict[str, JsonValue]` values.
The request is the formatted provider request object. Events are the native
Strands model stream, including reasoning and partial output, before downstream
validation. `failed(error)` records an interrupted or failed stream;
`completed()` records normal exhaustion. Observer failures propagate. The host
must retain these artifacts privately and distinguish partial from complete
streams. Process loss can prevent a terminal marker.

These logical artifacts are not HTTP bytes or raw provider SSE frames. For full
available provider evidence, use `RecordingOpenAITransport` with a host-owned
`StrandsHTTPObserver`. Its `record(event)` callback receives JSON-safe events with
unique event and exchange IDs. Requests contain the method, sanitized endpoint
URL, and base64 body. Responses contain status and allowlisted content/encoding
and provider request-ID headers. Each raw response chunk is base64 encoded before
the OpenAI or Strands decoder can discard fields. The stream is single-pass and
does not read ahead. Completion is emitted only at body EOF. Failure or
cancellation leaves a failure marker; process loss can leave no terminal marker.

The recorder never reads request headers. It excludes response authorization and
cookie headers and removes URL credentials and query parameters. Bodies can still
contain sensitive data and require private host storage. It does not change bytes,
retry requests, or infer successful model output from HTTP completion.

Use a caller-owned `openai.AsyncOpenAI` client with an HTTPX2 client and the
recording transport, then pass `client=...` to `ReasoningOpenAIModel`. Close that
client after the whole agent invocation, including all tool continuations. Do not
reuse `client_args.http_client` across the native provider's per-request client
contexts: those contexts close it after each turn. For AWS, the documented OpenAI
API-key callable can obtain a fresh short-lived token through the official
`aws_bedrock_token_generator.provide_token` on each request. No long-lived key is
needed. Credentials must use the host's approved AWS chain.

For providers such as Kimi that require `reasoning_content` on assistant turns,
select `model.with_reasoning_history(ReasoningHistoryMode.REQUIRED)`. The checked
enum also provides `PRESERVE`, the default. Preserve mode retains explicit empty
reasoning and omits absent fields. Required mode also supplies an empty protocol
field when the native SDK retained no reasoning block. This compensates for the
SDK's omission of incoming empty reasoning deltas; raw HTTP evidence retains the
original response. No reasoning text is invented. A reasoning-only assistant turn
with no ordinary content or tool call remains unsupported and fails explicitly.

Normalized C4A traces still omit raw prompts, tool values and responses. Their
closure describes normalized instrumentation, not raw transcript completeness.
AWS authentication, durable storage and redaction remain host responsibilities.

## Semantic Support

| Requested mapping | Strands outcome |
| --- | --- |
| Python tool, datasource, or external context | `exact` |
| Approval on a Python tool | `exact` through `HumanInTheLoop` |
| Agent structured output | `exact` through generated tool validation or native schema plus host validation |
| `delegate` with `history = none` | `emulated` by a typed agent-as-tool wrapper |
| Named-environment delegate isolation | Determined by the selected `EnvironmentProvider` |
| `delegate` with `summary` or `full` history | `unsupported` |
| Any handoff mode | `unsupported` |
| Tool isolation, remote, TypeScript, module, MCP, or provider-hosted locator | `unsupported` |

Delegate arguments originate with the model. The plan therefore records a host
obligation when the host must prove that a model-supplied argument equals the
declared source expression. Contract4Agents does not substitute Strands Swarm,
Graph, or Workflow behavior for a contract handoff.

Agents are constructed with `retry_strategy=None` and without a Strands session
manager. These constructor values are retained as generated-wrapper evidence;
the current SDK does not provide a reliable public readback accessor. They do
not prove host retry, cancellation, deadline, persistence, or fallback
behavior. Delegate tools use `child.as_tool(preserve_context=False)`, so each
delegate invocation starts from the child agent's construction baseline.
Applications requiring independent concurrent state must materialize
independent graphs or otherwise provide independent agent instances.

Portable list bounds and string bounds are enforced by the generated Pydantic
types and are included in the exact native schema evidence read during offline
materialization. This proves Strands runtime validation and schema conformance;
it does not claim that every Strands provider offers constrained decoding.

## Materialize and Run

```python
from contract4agents import materialize
system = materialize(
    "agent_contracts",
    target="strands",
    profile="production",
)

agent = system.agents["IncidentCommander"]
result = await agent.invoke_async(user_request)
output = system.validate_output_for_agent(
    agent,
    result.structured_output,
)
```

`system.agents` contains native Strands agents keyed by contract name.
`system.validate_output_for_agent(...)` fails closed if the native structured
output does not satisfy the selected agent's contract type.

A bound Python tool can return ordinary structural data or application
Pydantic models, including models nested in mappings and lists. The adapter
converts those models to ordinary Python data and then applies the generated
strict output adapter. Missing, extra, or incorrectly typed values still fail.
The shared host-callable boundary gives Python-mode arguments to the callable
and gives the validated JSON-mode result to Strands. Portable values such as an
aware `datetime` therefore remain Python objects inside application code.

An approval-required tool returns a normal Strands interrupt before the host
callable executes. The host presents the decision, records approval evidence,
and resumes the native agent with Strands `interruptResponse` content. The
materializer does not own the UI or decide whether approval is granted.

Datasource and external-context values are resolved through
`system.resolve_context_for_agent(...)`. The host then supplies those rendered,
typed values through its application-specific invocation strategy.

## Runtime Trace and Assurance

Attach the normalized router to a materialized graph once, then bind one
host-owned attempt around each invocation:

```python
from contract4agents.tracing import (
    StrandsNormalizedTraceRouter,
    TraceAttempt,
)

router = StrandsNormalizedTraceRouter()
router.attach(system)
agent = system.agents["IncidentCommander"]
session = router.open_session(system, run_id=run_id)
attempt = TraceAttempt("incident:1", "incident:attempt:1", 1)

with session:
    with session.bind_attempt(attempt, agent=agent):
        result = await agent.invoke_async(prompt)

snapshot = session.closed_snapshot
trace = snapshot.trace
trace_closure = snapshot.closure
```

The bridge uses public Strands hooks and maps native agent, contract tool, and
delegate identities back to semantic IDs. Nested child-agent invocations remain
part of the bound host attempt. Raw prompts, tool arguments, and results are not
copied into normalized trace payloads.

For an approval pause, record the request and decision against the corresponding
native grant object before resuming. Pass the stable native tool-use ID as
`provider_identity` to both methods. It must match the ID on the later tool hook
event so assurance can join that exact invocation to its approval. If the host
cannot supply this identity, approval assurance remains unverified. Closing the
session produces
`TraceClosureEvidence`; missing, failed, or unbound instrumentation remains
incomplete or unverified and cannot support assurance claims.

Contract4Agents does not install a runner, callback handler, session manager,
retry loop, observability backend, or deployment controller. Those remain host
or platform responsibilities.

## Provider Evidence

The trace bridge reports successful `AgentResult` usage from the public
`metrics.accumulated_usage` shape. It records input, cached input, output, and
total tokens when available and binds the aggregate to the host attempt. A
missing `AgentResult` usage object is unavailable. A provider model hook
exception reports a terminal failed outcome with unavailable usage, while the
response path remains unverified.

`stop_reason` can report provider cancellation or refusal only when it contains
the corresponding stable public value. `interrupt` and `checkpoint` are
interruption evidence, not provider cancellation. Hook exceptions, prompts,
tool arguments, results, and exception text are never copied into normalized
events. The bridge does not retry, decide retryability, calculate cost, or
choose a fallback model.
