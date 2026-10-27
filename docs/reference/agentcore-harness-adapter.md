# AgentCore Harness Target

The optional `agentcore_harness` target derives managed agent configuration
from contracts and binds typed client handles to verified deployed agents.
Amazon Bedrock AgentCore Harness supplies the agent loop. AgentCore Runtime
hosts application code; it is a different integration.

The contract owns instructions, types, grants, and composition. Target bindings
select models, resource references, and supported provider settings. The
application owns authentication, business authorization, approvals, sessions,
tool execution, recovery, and external effects. The deployment system owns AWS
writes, permissions, promotion, rollback, and endpoint stability.

## Supported Route

| Feature | Support and mechanism |
| --- | --- |
| Model | Explicit Bedrock model through `converse_stream` |
| Deployment artifact | AWS CLI `create-harness --cli-input-json` request files and an identity manifest |
| Instructions | Derived from the contract and generated final-output schema |
| Typed input | Strict generated model before invocation |
| Typed return | Host-enforced JSON and type validation; invalid output fails explicitly |
| Native constrained generation | Unverified; this route makes no constrained generation claim |
| Python tools | Inline functions, strict arguments and results, explicit tool allowlist |
| Approval | Application decision bound to the complete pending call and context |
| Delegation | Fixed declared child, `history = none`, exact typed input mappings |
| Handoff | Unsupported |
| Memory and truncation | Memory explicitly disabled; truncation explicitly `none` |
| Explicit isolation profiles | Unsupported |
| Declared context and datasources | Unsupported; use typed input and inline tools |
| MCP, Gateway, Browser, Code Interpreter | Unsupported in this route |
| Zero enabled tools | Unsupported until a supported deny-all mapping is established |
| Skills and lifecycle hooks | Unsupported; deployment verification rejects them |
| Invocation overrides | Nonempty overrides rejected; select a new reviewed profile |
| Retries and fallback | Application-owned; adapter-created SDK clients disable retries |

Required unsupported mappings block target conformance or planning. An allowlist
restricts model tool selection. It does not prove filesystem, network, secret,
or tenant isolation. Callers must not receive the separate
`bedrock-agentcore:InvokeAgentRuntimeCommand` permission. IAM policy remains a
deployment obligation.

## Offline Configuration

Install the optional AWS client for repository work with
`pdm install -G agentcore-harness`. Planning and generation need no AWS client.
Binding inspection can import trusted Python modules and execute their
module-level code.

Tool bindings accept `python` and an optional exact `tool_name`. Generated
names are stable when `tool_name` is omitted. Wildcards, built-in names, and
name collisions are rejected. Each agent needs one to 64 inline tools,
including its outgoing delegations.

Profiles accept `execution_role_arn`, `harness_name`, `max_iterations`,
`max_tokens`, `timeout_seconds`, `temperature`, and `top_p`. The only accepted
`api_format` is `converse_stream`; the only accepted `output_mode` is
`host_validation`. Defaults are 10 iterations, 4096 tokens per iteration, and
120 seconds. These settings do not prove a total business budget or host
deadline. Store credential references rather than secret values.

```bash
pdm run contract4agents harness-config examples/harness-research \
  --target harness --profile test --out .contract/harness
```

Each `<Agent>.harness.json` file is standard AWS CLI input. `manifest.json`
records contract, plan, and configuration digests. Review these files and pass
them to your deployment pipeline. Generation makes no AWS calls. See the
[worked example](../../examples/harness-research/README.md).

## Deployment Verification and Materialization

Use `generate_harness_configuration(...)` to obtain reviewed configuration.
Call `verify_harness_deployment(configuration, agent_name, harness_arn=...,
endpoint_name=..., client=...)` explicitly for each deployed agent. It reads
the named endpoint, requires stable `READY` versions, reads that concrete
Harness version, compares the effective configuration, and reads the endpoint
again to detect changes. It rejects unreviewed skills, hooks, environment
variables, custom artifacts, and custom inbound authorizers in this route.
It accepts the computed default runtime identity and public network settings,
records an environment digest, and rejects mounted filesystems or unsupported
network settings. This does not establish filesystem or network isolation.

Pass those records to
`AgentCoreHarnessMaterializationProvider(deployments=records, client=runtime)`
and use the normal `materialize(...)` API. Missing or mismatched evidence blocks
materialization. Tags and ARNs alone do not prove equality. Materialization
makes no AWS calls and changes no resources.

Verification is an observation. Invocation selects an endpoint name, which can
change after verification. The deployment system must keep the endpoint stable
while a binding is used. Reverify after deployment changes. Verification does
not prove IAM policy or external effects. Injected SDK clients must preserve
the documented retry boundary.

AWS can return an empty optional truncation configuration when the strategy is
`none`. Adapter-created control clients remove that empty field before SDK
parsing. The effective strategy and all other configuration fields stay intact.
An empty configuration for an active truncation strategy still fails parsing.
Injected control clients must handle this service response themselves.

## Typed Invocation and Continuation

`system.agents[agent_name]` contains a `HarnessAgent`. Supply a
`HarnessRunContext` with an authenticated principal, authorization context,
session ID, run ID, thread ID, and existing `TraceAttempt`. Session IDs need
33 to 100 letters, digits, underscores, or hyphens. Supply a fresh session for
a new call. A new Python handle does not establish a fresh AWS session.

`agent.invoke(inputs, context=context)` reads the complete stream and returns:

- `HarnessCompleted`: a validated typed value and available request ID.
- `HarnessInterrupted`: one typed pending call for application handling.
- `HarnessFailed`: a content-free failure reason and available provider facts.

Failures distinguish refusal, timeout, token or iteration limits, transport,
authentication, authorization, rate limits, invalid output, invalid tool input,
ungranted tools, and incomplete execution. Input, identity, approval, and result
validation errors raise before the affected AWS call. There is no automatic
retry, JSON repair, or model fallback.

No tool executes from a fragment. A pending call requires complete blocks and
the final `tool_use` stop reason. For adjacent inline calls, submit only the
first pending result and wait for the next handoff. Early content for later
calls does not authorize execution.

Use `agent.tool_arguments(...)` to obtain validated Python arguments. The
application decides approval and calls `agent.record_approval(pending, decision,
context=context)` before executing the operation. It then calls
`agent.resume(pending, value, context=context, approval=decision)`. An
approval-required tool needs a `HarnessApproval` with the exact
`pending.call_digest`, the decision, and a host evidence reference. Denial
submits an error result without accepting a tool value.

The digest binds agent, contract, plan, configuration, deployment, session,
principal, authorization context, attempt, tool-use ID, and arguments.
Continuation validates the result and uses the original tool-use ID and
session. A submitted result does not prove the external action occurred.

Persist `pending.to_dict()` only in authenticated application storage. It
contains customer input and arguments. Recover with
`HarnessPendingCall.from_dict(...)` and explicit `agent.restore_pending(...)`.
Supply any persisted approval to `restore_pending(..., approval=decision)`
and retain its original trace evidence. Restoration does not invent a new
approval event. The host can correlate actual tool events with the approval by
using `pending.call_digest` as `provider.span_id` and the same trace attempt.
The application must prevent stale state, unauthorized restoration, and replay
across processes. The handle prevents duplicate submission within its process.
A transport failure after submission leaves the remote outcome uncertain;
recovery must not repeat an external action automatically.

## Delegation

`parent.invoke_delegate(pending, child, context=parent_context,
child_context=child_context)` accepts only the declared child with the same
contract and plan. Mappings must be simple `input.<path>` references. Model
arguments must equal their values in the original typed parent input. The
model cannot select an arbitrary agent ARN.

Supply a fresh child session and invocation identity, preserve the principal
and authorization context, and set child `parent_event_id` to
`pending.event_id`. The child receives only typed mapped input. Return its
validated final value through the parent's `resume(...)`. The application
handles interrupted and failed children and owns deterministic pipelines.

## Trace and Assurance

Pass the existing `normalized_trace_sink` to `materialize(...)`. Events retain
contract, plan, semantic, run, and attempt identity. Deployment provenance
contains configuration digest, ARN, endpoint, and observed version. Prompts,
arguments, output, principal values, and secrets are omitted.

The adapter records requests and result submission. The host records actual
tool start, completion, and effect evidence where required. Client submission
is not a completion attestation. Delegation retains parent/child event links.

Usage has response scope and partial coverage, with no inferred model-request
count. Identical metadata within a stream is counted once; ambiguous multiple
aggregates remain unverified. Resumed calls have separate aggregation identity.
Missing usage stays unavailable.

Streams do not prove instrumentation closure for the managed loop. Missing
closure, deployment proof, approval evidence, or provider facts must remain
unavailable or unverified in assurance. Successful invocation and graph
validation alone cannot establish a passing assurance result.

## Provider Evidence and Validation

Offline tests use scripted streams and installed botocore request and event
shapes. They do not prove AWS authentication, regional model access, model
behavior, or deployed isolation. See the opt-in
[live Harness check](../quality/validation.md#live-agentcore-harness-check).

Provider references checked on 24 October 2026:

- [Tools and inline continuation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-tools.html)
- [Memory defaults](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-memory.html)
- [InvokeHarness](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeHarness.html)
- [GetHarness](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_GetHarness.html)
- [GetHarnessEndpoint](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_GetHarnessEndpoint.html)

The optional client dependency requires boto3 1.43.118 or later. Verify AWS
support and model access before deploying generated requests.
