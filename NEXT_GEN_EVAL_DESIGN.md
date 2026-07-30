# Next-Generation Evaluation Design

**Status:** revised proposal aligned with the current materialization, tracing,
and host-owned workflow architecture.

## Current Implementation Boundary

The public `eval` command currently performs deterministic replay assessment.
It loads `FileEvalProvider`, reads prewritten inputs, outputs, normalized trace
events, closure evidence, approval decisions, and judge decisions from
`eval-data.json`, then passes that evidence to `run_campaign(...)`.

It does **not** materialize or invoke a native agent graph.

That behavior is useful and should remain supported, but it must be named
`replay`. This proposal adds a separate execution path that invokes the same
reviewed graph used by normal materialization.

The repository already supports three native target adapters:

- OpenAI Agents SDK;
- Strands Agents; and
- Google Agent Development Kit.

The public design must account for all three now. A first vertical slice may
land on one adapter, but no OpenAI-specific assumption belongs in the portable
campaign, lifecycle, input, or artifact models.

## Thesis

Contract4Agents should materialize evaluation execution in the same way it
materializes agent graphs:

```text
portable contract and eval cases
  -> eval-environment overlay
  -> one authoritative effective materialization plan
  -> target-native graph and trial executor
  -> identity-bound trace and closure snapshot
  -> shared assessment
  -> versioned campaign artifact
```

A user who has configured a target, profile, and safe eval environment should
not have to implement the entire `EvalProvider` protocol merely to execute
their contract's `.eval` cases.

This remains a contract-and-assurance system. It is not a hosted eval service,
a universal agent runtime, or a workflow engine.

## Accepted Architectural Boundaries

The implementation must preserve these existing decisions:

1. **The host owns deterministic workflow.** Host code owns branching, loops,
   retries, transformations, transactions, persistence, recovery, and terminal
   selection. Run specs declare and assess evidence about that workflow; they
   do not execute it.
2. **Materialization is target-native.** OpenAI, Strands, and Google ADK retain
   their native execution models. Portability applies to the contract,
   execution request, normalized evidence, and assessment result—not to every
   provider mechanism.
3. **One effective plan governs execution and evidence.** Eval overrides apply
   before planning, implementation import, graph construction, and trace
   identity are finalized. A graph may not be built from a base plan and
   patched afterward.
4. **Tracing already owns attempt evidence, not retry policy.** Existing trace
   sessions provide attempt identity, retry-chain validation, terminal
   selection, snapshots, and closure. Eval execution must reuse these
   primitives rather than create a parallel lifecycle vocabulary.
5. **In-process execution is not a sandbox.** Sealed dependency resolution can
   prevent accidental production fallback, but it does not enforce operating
   system filesystem, network, process, or secret isolation.
6. **Assessment does not enforce business policy.** Contracts, controls,
   judges, approvals, and traces produce reviewable evidence. The host remains
   responsible for authorization, transactional policy, idempotency, and final
   safety checks.

## Design Goals

1. Provide an explicit command and Python API that invoke a configured native
   agent graph.
2. Preserve the current file-backed path as honestly named replay assessment.
3. Reuse normal parsing, semantic analysis, planning, materialization, runtime
   resolution, tracing, closure, and shared assurance.
4. Require users to supply only irreducibly application-specific fixtures,
   test doubles, approval policy, and optional judge policy.
5. Fail before execution when an eval environment is incomplete, unsafe, or
   incompatible with the selected target.
6. Structurally separate model-visible invocation inputs, host-only context,
   evaluator-only truth, and exported report views.
7. Give every campaign execution, trial execution, invocation, attempt,
   approval request, trace, and artifact a causal identity.
8. Keep execution status separate from behavioral assessment status.
9. Produce versioned, identity-validated artifacts suitable for assurance
   bundling and deliberate baseline comparison.
10. Define one small cross-target trial-executor contract that OpenAI, Strands,
    and Google ADK can implement without hiding meaningful differences.

## Non-Goals

- Running arbitrary customer workflows without an application integration
  point.
- Replacing host-owned run-spec execution, retry, persistence, or recovery.
- Inventing fake EHR, billing, CRM, payment, or other domain behavior.
- Treating a semantic judge as an access-control or business-policy engine.
- Running production credentials, production write tools, or sensitive data by
  default.
- Claiming whole-trial isolation when execution is only in-process.
- Hiding target differences behind a misleading universal runtime.

## Public Product Shape

Evaluation has two explicit top-level operations:

```bash
contract4agents eval replay agent_contracts \
  --target openai \
  --profile test \
  --data eval-data.json
```

```bash
contract4agents eval run agent_contracts \
  --target strands \
  --profile test \
  --environment local_fixtures \
  --model-source scripted \
  --trials 3 \
  --out .contract/eval-run
```

`eval replay` assesses supplied evidence and never claims to invoke an agent.
`eval run` materializes and invokes a native graph.

There is no compatibility alias preserving the current ambiguous
`contract4agents eval ...` meaning. This is a deliberate pre-1.0 command
migration.

### Independent execution dimensions

`replay`, `materialized`, and `live` must not be represented as interchangeable
values of one mode field. The plan records independent dimensions:

| Dimension | Examples | Meaning |
| --- | --- | --- |
| execution source | `replay`, `native` | Whether evidence is supplied or produced by invocation |
| model source | `scripted`, `provider` | Whether model behavior is deterministic or provider-backed |
| dependency posture | `local`, `sandbox`, `external` | Where tools, datasources, and external context execute |
| side-effect posture | `denied`, `sandboxed`, `explicit_live` | Which observable writes are permitted |
| isolation posture | `in_process`, provider-specific sandbox identity | What boundary is actually enforced |

A provider-backed model does not imply production dependencies. A scripted
model does not imply whole-trial isolation. A native graph with test doubles is
an integration eval, not a model-behavior eval.

## Materialization Result

Add a public entry point, with final naming settled during implementation:

```python
result = materialize_eval_runner(
    root,
    *,
    target: str,
    profile: str,
    environment: str,
    model_source: str,
    bindings: TargetBindings | Path | str | None = None,
    eval_bindings: EvalBindings | Path | str | None = None,
)
```

It returns an immutable result:

```python
@dataclass(frozen=True)
class EvalMaterializationResult:
    eval_plan: EvalMaterializationPlan
    system_factory: TrialSystemFactory
    runner: MaterializedEvalRunner
```

`eval_plan` binds the effective materialization plan to the eval environment,
execution dimensions, fixture source, policies, measurement settings, and
adapter capabilities. `system_factory` creates a normal `MaterializationResult`
from that plan with trial-scoped runtime services. `runner` executes canonical
`.eval` cases and produces a versioned campaign artifact.

The result does not expose one mutable `MaterializationResult` for unconditional
reuse across every trial. The current native graph contains a mutable
`ContextRuntime`, including run and thread caches, so reuse requires an explicit
adapter attestation and fresh trial-scoped runtime state.

The central invariant is:

> A materialized eval runner may not construct a second agent registry, tool
> graph, instruction set, grant set, or output schema.

## Eval Environments and the Effective Plan

### Configuration authority

An `EvalEnvironment` is a named, target-specific implementation overlay. It may
select:

- fixture source;
- tool, datasource, and external-context implementation replacements;
- model source and target-supported model driver;
- approval policy;
- quality judge;
- runtime or sandbox provider;
- side-effect and network posture; and
- measurement policy.

It may not declare or change:

- agents, instructions, guidance, or composition;
- portable tool or datasource interfaces;
- grants or authorization semantics;
- controls, qualities, or operational controls;
- output schemas; or
- declared isolation requirements.

Eval environments live in the dedicated companion file
`contract4agents.evals.toml` by default, with an explicit path override in the
CLI and Python API. Keeping test-only implementation locators and policies out
of `contract4agents.targets.toml` preserves the production binding boundary.
Each named environment declares its target and overlays only that target's
implementation selections. The loader produces a distinct `EvalEnvironment`
model rather than passing an opaque mapping through to user code.

The version `1` shape should be explicit and semantic-ID-addressed:

```toml
schema_version = "1"

[environments.local_fixtures]
target = "strands"
dependency_posture = "local"
side_effect_posture = "denied"
isolation_posture = "in_process"

[environments.local_fixtures.model]
source = "scripted"
factory = "acme_agent.evals.models:scripted_model"

[environments.local_fixtures.fixture_source]
python = "acme_agent.evals.fixtures:source"
version = "incident-fixtures-v3"

[environments.local_fixtures.tools."status.publish"]
python = "acme_agent.evals.tools:record_publish"

[environments.local_fixtures.approval_policy]
python = "acme_agent.evals.approvals:policy"
version = "publish-policy-v2"

[environments.local_fixtures.quality_judge]
python = "acme_agent.evals.judges:judge"
policy = "eval-assets/incident-quality.md"
```

Every locator is validated and recorded individually. There is no opaque
`overrides` callable that can replace undeclared dependencies at runtime.

### Planning order

Planning follows one order:

```text
load contract and target bindings
  -> select eval environment
  -> validate overlay authority
  -> resolve effective bindings
  -> compute the effective MaterializationPlan
  -> import only implementations named by that plan
  -> build the native graph
  -> bind trace identity to that plan digest
```

The base binding and overlay remain inspectable inputs, but the effective plan
digest is the only plan identity used by the graph, trace, trial, assessment,
and artifact.

Changing any selected implementation, model driver, policy, or enforcement
posture changes the eval-plan digest. Changing a fact that affects the native
graph also changes the effective materialization-plan digest.

### Sealed resolution and real isolation

Sealed resolution means:

- every external boundary resolves through the selected eval environment;
- missing replacements fail instead of falling back to production bindings;
- production environment variables and secrets are not inherited by
  convention;
- side-effecting capabilities are denied unless the plan names a safe double or
  an explicitly authorized sandbox implementation;
- missing approval policy denies the request or makes required evidence
  unverified; and
- missing judge evidence leaves the quality result unverified.

Sealed resolution is not an operating-system security claim. The eval plan must
record the isolation provider and the dimensions it actually enforces.
`InProcessEnvironment` must be reported as `in_process`; it cannot claim
filesystem, network, process, or secret isolation.

## Typed Trial Data and Audience Separation

The parser and semantic model must understand eval givens well enough to map
them to the selected entry agent's typed invocation parameters. Arbitrary
strings merged into a generic input object are not sufficient for native
execution.

Fixture resolution produces structurally separate channels:

```python
@dataclass(frozen=True)
class ResolvedTrialData:
    invocation: InvocationInputs
    host_context: HostFixtureContext
    evaluator_truth: EvaluatorTruth
    report_view: RedactedTrialView
```

- `invocation` contains only validated entry-agent arguments.
- `host_context` contains fixture values available to approved test doubles and
  runtime providers, not automatically to the model.
- `evaluator_truth` contains expected answers and scorer-only facts. It is
  never included in an execution request, model context, generic trace event,
  or default report serialization.
- `report_view` is an audience-classified, redacted projection created
  explicitly for export.

Fixture references such as `TypeName.fixture("name")` resolve through a typed
fixture source and are validated against canonical contract types before a
trial enters `running`.

Judge input is another explicit audience projection. Evaluator truth or
sensitive host context reaches a judge only when the environment authorizes
that exact boundary and records the disclosure posture in the eval plan.

## Narrow Application Hooks

Ordinary users should configure small interfaces instead of implementing one
object that resolves inputs, executes agents, approves actions, and judges
quality:

- `FixtureSource` resolves named, versioned fixture values.
- `ApprovalPolicy` decides one exact typed approval request.
- `QualityJudge` assesses one declared rubric against an audience-safe result
  view.
- Tool, datasource, and external-context replacements use the existing runtime
  callable contracts.
- `ApplicationTrialExecutor` is an advanced seam only for a host-owned outer
  workflow.

Every hook has a stable identity or version and a canonical policy/configuration
digest. Authored judge prompts and policies live in external versioned,
reviewable assets rather than inline implementation strings. The eval plan and
campaign artifact retain their digests and data-handling posture.

## Cross-Target Trial Executor

The adapter-neutral contract should be narrow:

```python
class EvalTrialExecutor(Protocol):
    async def execute(
        self,
        request: MaterializedTrialRequest,
    ) -> MaterializedTrialEvidence: ...
```

`MaterializedTrialRequest` contains:

- a `MaterializationResult` created by the trial system factory;
- the immutable eval plan;
- campaign, trial, invocation, and initial-attempt identities;
- validated invocation inputs;
- approved host fixture context;
- approval, trace-session, and measurement services; and
- cancellation and deadline signals.

It does not contain evaluator truth.

`MaterializedTrialEvidence` contains:

- native terminal output;
- immutable trace and closure snapshot from one exact frontier;
- terminal attempt selection;
- execution timestamps and metrics;
- provider correlation references;
- optional host run-spec evidence; and
- explicit adapter diagnostics and degraded/unsupported facts.

The adapter registration should expose an optional eval-executor factory and
its eval capabilities. Planning fails before trials begin when the selected
adapter lacks a required executor or capability.

The existing `EvalProvider` remains a lower-level advanced seam for replay,
remote execution, and organization-specific integrations. It is not the normal
materialized-eval user interface.

### Target-specific responsibilities

Each current adapter must define:

1. how it invokes an entry agent;
2. how it supplies model configuration or a supported scripted driver;
3. how it intercepts or observes approval;
4. how it creates and binds attempts;
5. how it normalizes provider response and exception evidence;
6. how it selects a terminal result;
7. how it closes or snapshots the trace session;
8. which latency, token, and cost metrics it can attest; and
9. which features are unsupported, degraded, emulated, or host-enforced.

The public request and evidence types remain portable; invocation mechanics do
not.

Model factories are target capabilities, not a portable assumption. Strands
and Google ADK can currently select target-bound model factories; OpenAI does
not expose the same seam. The plan must reject an unsupported `model_source`
rather than silently substituting a live provider model.

## Direct Agent Runs and Host-Owned Workflows

The built-in executor handles the common case: invoke one declared entry agent
with typed inputs.

Contract4Agents does not automatically execute a declared run spec or infer an
application workflow from composition. If an eval must exercise deterministic
outer workflow, the application supplies an advanced trial executor that:

- calls the materialized agents from host code;
- owns branching, retry, persistence, recovery, and terminal selection;
- uses the existing Contract4Agents trace-session and `TraceAttempt`
  primitives;
- closes the session and returns an immutable snapshot rather than a live
  session object; and
- supplies `RunSpecEvidence` for separate `assess_run_spec(...)` assessment
  when a run spec applies.

The campaign runner assesses the returned evidence. It does not take ownership
of the host workflow or replay workflow decisions itself.

## Campaign and Trial Lifecycle

Execution identity and assessment status are separate.

### Required identities

- `campaign_id`: stable logical campaign definition selected by the user.
- `campaign_execution_id`: unique identity for one attempted campaign run.
- `case_id`: canonical eval semantic ID.
- `trial_execution_id`: unique identity for one trial execution.
- `invocation_id`: identity for one logical agent or host-workflow invocation.
- `attempt_id`: identity for one attempt in a validated retry chain.

Repeating a campaign or resuming an interrupted campaign does not reuse trial
execution IDs accidentally. An explicit idempotency key may intentionally bind
a retry or resume to existing persisted state.

### Execution state

At minimum:

```text
planned -> running -> succeeded
                   -> failed
                   -> cancelled
                   -> timed_out
                   -> invalid_evidence
```

Assessment remains:

```text
passed | violated | unverified
```

A trial can therefore be `succeeded/unverified` when execution completed but a
judge or required trace channel is unavailable. A provider exception is
`failed/unverified`, not merely an assurance status with its execution history
discarded.

### Persistence and recovery

- Persist the campaign manifest before the first trial starts.
- Persist each state transition and finalized trial artifact atomically.
- Never wait for the entire campaign before writing the only durable result.
- Resume only after validating contract, effective plan, eval plan, fixture,
  policy, and artifact identities.
- Reuse a completed trial only under an explicit matching idempotency key.
- Preserve partial trace and closure evidence on failure, cancellation, or
  timeout.
- Treat contradictory identity or closure evidence as `invalid_evidence` and
  make the campaign fail closed.

The compiled contract and immutable plans may be reused. Mutable context,
caches, trace sessions, invocation state, and attempt state are fresh per
trial. A native graph may be reused only when its adapter attests that the graph
is stateless and all mutable runtime services remain trial-scoped.

## Approval Causality

Approval evidence must authorize one exact action, not merely an earlier use of
the same capability.

An approval request identity binds:

- campaign and trial execution IDs;
- invocation and attempt IDs;
- agent ID;
- grant ID;
- capability ID;
- provider tool-call or request ID;
- canonical argument digest;
- approval-policy identity and version/digest;
- issuance time and optional expiry; and
- immutable evidence references.

The decision repeats the request digest and records allow/deny, reason,
decision time, policy identity, and evidence references.

A tool start satisfies an approval-required control only when it matches the
exact approved request, occurs after an unexpired allow decision, and belongs
to the same invocation and attempt. Approval from an earlier failed attempt
cannot authorize a retried call unless the host explicitly issues a new
request or a declared policy permits and records transfer.

## Trace Ownership and Closure

The component that invokes the target owns the disposable trace session:

- the built-in direct executor opens, binds, snapshots, and closes it;
- an advanced host executor does the same around its workflow; and
- replay loads already-finalized trace and closure evidence.

The campaign runner accepts only an immutable trace/closure snapshot. It never
accepts a live session or a best-effort trace dumped later.

Finalization occurs only after native invocation has ended and the adapter has
established instrumentation quiescence. A no-op flush or an arbitrary delay is
not closure evidence. An adapter that cannot establish the required frontier
returns incomplete closure, making absence-dependent claims `unverified`.

Every trial trace is bound to the campaign execution, trial execution, contract,
effective materialization plan, and eval plan. Existing run and thread identity
remain valid provider-neutral trace fields; the eval artifact manifest carries
the enclosing campaign/trial identities. The built-in direct executor uses the
trial execution ID as the normalized trace `run_id`, avoiding a second
unexplained per-trial identity.

Incomplete instrumentation makes absence-dependent results `unverified`.
Closing after an exception preserves incomplete evidence; it does not convert
absence into proof.

## Assessment Semantics

Materialized and replay paths converge only after they have produced valid,
identity-bound evidence.

The shared sequence is:

1. validate output shape;
2. validate trace, plan identity, attempt chain, terminal selection, and
   closure;
3. evaluate deterministic eval expectations;
4. call the existing shared behavioral control assessor;
5. assess declared operational controls from attested execution evidence;
6. call configured quality judges with an audience-safe view; and
7. derive the behavioral assessment status separately from execution status.

### Operational controls

Operational controls are contract declarations, not aliases for campaign
thresholds.

- Per-trial latency, cost, token, and retry controls use attested trial metrics
  and attempt evidence.
- Volume or cross-run controls use the complete campaign artifact.
- Missing measurement or attempt evidence produces `unverified`.
- An adapter that cannot attest a required metric fails planning when the
  limitation is knowable in advance.
- Campaign thresholds remain release policy over aggregate results. They do not
  satisfy or replace a declared operational control.

The language and assessor must define the aggregation semantics for every
supported operational expression before claiming support. Unsupported
expressions fail closed rather than being silently ignored.

## Versioned Artifacts

### Campaign manifest

Materialized and replay campaigns produce a versioned manifest. Version `1`
must include:

- campaign and campaign-execution identities;
- contract digest;
- effective materialization-plan digest;
- eval-plan and eval-environment digests;
- target, profile, adapter version, and execution dimensions;
- model-source identity and provider/model options safe for export;
- fixture dataset identity/version without raw evaluator truth;
- approval-policy and judge identities/digests;
- measurement and redaction-policy identities;
- trial selection, count, seed or deterministic ordering policy;
- per-trial execution and assessment status;
- trace, closure, output, metrics, assessment, and diagnostic artifact digests;
- campaign summary and threshold results; and
- persistence/completion status.

Large or sensitive evidence remains in separately digested artifacts. The
manifest references it rather than copying raw invocation input, evaluator
truth, provider payloads, or sensitive trace fields into every report.

Assurance bundle assembly must parse and validate the supported schema version,
contract identity, effective plan identity, eval-plan identity, completion
state, and referenced artifact digests. An arbitrary JSON object cannot count
as verified eval evidence.

Version `1` uses a directory artifact:

```text
eval-run/
  manifest.json
  trials/
    <trial-execution-id>/
      result.json
      trace.jsonl
      closure.json
      assessments.json
```

The manifest is the commit point for referenced trial artifacts. An append-only
execution ledger or alternate artifact store may be added later without
changing the manifest semantics.

### Baseline comparability

A baseline is not comparable merely because it has a digest and aggregate
rates.

The campaign computes a comparability identity from:

- eval environment and effective dependency implementations;
- target adapter and model-source posture;
- fixture dataset and trial-selection policy;
- approval and judge policies;
- measurement, redaction, and aggregation policies; and
- any other field designated invariant by the selected baseline policy.

The baseline policy explicitly names which subject-under-test fields may
differ. For example, a release comparison may deliberately allow the contract,
effective plan, or provider model version to change while requiring the same
fixtures, judge, dependency posture, and measurement policy.

If a required invariant differs, the comparison result is `incomparable`.
Thresholds and regression tolerances are not applied to incomparable evidence.
The report explains every differing identity. The default policy is strict;
allowing differences requires a named, digested policy.

## Failure Semantics

| Condition | Execution status | Assessment consequence |
| --- | --- | --- |
| Required plan mapping or eval capability is unsupported | campaign does not start | no trial assessment |
| Eval environment resolves an unsafe or production dependency | campaign does not start | no trial assessment |
| Native invocation completes with conforming evidence | `succeeded` | assess normally |
| Required expectation or control is disproven | `succeeded` | `violated` |
| Approval is denied and no prohibited action occurs | `succeeded` | depends on declared expectations and applicability |
| Provider, tool, fixture, or host-workflow failure | `failed` | `unverified` unless captured evidence proves a violation |
| Judge failure after valid execution | `succeeded` | affected quality result is `unverified` |
| Deadline expires | `timed_out` | `unverified` unless captured evidence proves a violation |
| User cancellation | `cancelled` | `unverified` unless captured evidence proves a violation |
| Trace identity, attempt chain, or closure is contradictory | `invalid_evidence` | fail closed; assurance refuses the trial |
| Required negative assertion lacks complete closure | unchanged | assertion is `unverified` |
| Test double attempts a disallowed side effect | `failed` or `invalid_evidence` | explicit violation when evidence establishes the attempt |

Campaign policy decides whether independent remaining trials continue after a
normal failed or timed-out trial. Identity corruption, unsafe dependency
resolution, or evidence contamination aborts the campaign.

## Security Defaults

- Eval environments are explicit, named, versioned, and digested.
- Every external boundary resolves through the effective eval plan.
- Production fallback is forbidden.
- Side-effecting capabilities default to denied.
- Live side effects require an explicit posture, implementation, policy, and
  user acknowledgement.
- Fixture values are typed and audience-classified.
- Evaluator truth is structurally absent from invocation requests.
- Raw sensitive inputs are absent from default report serialization.
- Judges receive only a permitted redacted view.
- Network and secret isolation are claimed only when an identified environment
  provider enforces them.
- Live-provider runs report model, cost, data, and retention posture.
- Assurance bundles remain evidence packages, not compliance certification.

## Python API

```python
materialized = materialize_eval_runner(
    "agent_contracts",
    target="strands",
    profile="test",
    environment="local_fixtures",
    model_source="scripted",
)

artifact = await materialized.runner.run(
    trials=3,
    thresholds=CampaignThresholds(min_pass_rate=0.95),
)
```

Replay remains explicit:

```python
artifact = await assess_replay_campaign(
    "agent_contracts",
    target="openai",
    profile="test",
    data="eval-data.json",
)
```

Lower-level provider and assessor APIs remain available for adapters,
application integrations, and tests.

## Migration

1. Freeze and document the current file schema as replay input.
2. Add versioned campaign artifact and lifecycle models shared by replay and
   native execution.
3. Add typed trial-data channels and remove evaluator truth from public trial
   serialization.
4. Add `EvalEnvironment`, overlay authority validation, and effective-plan
   digesting before any native executor ships.
5. Add the adapter-neutral executor contract and capability reporting to the
   existing adapter registry.
6. Implement at least one full native vertical slice, then implement the same
   public contract for OpenAI, Strands, and Google ADK without changing the
   portable models.
7. Replace the CLI with explicit `eval replay` and `eval run` commands.
8. Update README, CLI, eval-language, quality, and example documentation in the
   same change. No page may describe replay as agent execution.
9. Reject unversioned or identity-incomplete eval results in new assurance
   bundles.

## Implementation Tranches

### Tranche 1: identity, data, and artifacts

- Define campaign/trial execution identity and state transitions.
- Define typed invocation, host-context, evaluator-truth, and report channels.
- Define the versioned campaign manifest and per-trial artifacts.
- Add comparability policy and `incomparable` results.
- Add incremental atomic persistence and resume validation.

### Tranche 2: eval environments and planning

- Define and load `EvalEnvironment`.
- Validate overlay authority and sealed dependency coverage.
- Compute the effective binding and one authoritative materialization plan.
- Add eval-plan visualization and semantic-diff coverage.
- Record model, side-effect, dependency, isolation, policy, and measurement
  posture.

### Tranche 3: cross-target execution

- Extend adapter registration with eval-executor capabilities.
- Reuse existing materialization and provider-neutral trace sessions.
- Implement the built-in direct-entry executor contract.
- Add exact approval-request correlation.
- Prove native local/scripted execution where supported and explicit
  provider-backed execution where required.

### Tranche 4: assessment and assurance

- Integrate operational-control assessment.
- Add audience-safe judge requests and policy provenance.
- Validate campaign artifacts during assurance assembly.
- Preserve behavioral, run-spec, operational, and quality results as distinct
  evidence types.

### Tranche 5: CLI, examples, and hardening

- Introduce `eval replay` and `eval run`.
- Convert one public example to a complete native local-fixture campaign while
  retaining replay coverage.
- Add timeout, cancellation, retry, resume, crash, isolation, and concurrency
  tests.
- Add full-path acceptance tests for OpenAI, Strands, and Google ADK.

## Validation Requirements

The implementation is incomplete until tests prove:

- typed fixture resolution and entry-parameter validation;
- structural absence of evaluator truth from execution, trace, judge, and
  default report views;
- complete replacement of production dependencies by a sealed environment;
- honest failure of unsupported isolation claims;
- one effective plan digest across graph, trace, trial, and artifact;
- native entry-agent invocation for every supported target;
- adapter-specific scripted/provider model-source validation;
- fresh mutable runtime and trace state per trial;
- retry-chain and terminal-attempt selection;
- approval allow, deny, expiry, argument mismatch, wrong attempt, and
  out-of-order behavior;
- closure and negative-assertion semantics after success, exception, timeout,
  and cancellation;
- instrumentation quiescence before the final trace/closure snapshot;
- separate execution and assessment statuses;
- atomic trial persistence and identity-validated resume;
- operational-control assessment from attested metrics;
- audience-safe judge and report redaction;
- versioned assurance ingestion and artifact-digest validation;
- strict and policy-authorized baseline comparison, including
  `incomparable`; and
- replay behavior after the explicit CLI migration.

At least one public fixture per target must prove:

```text
contract
  -> eval overlay
  -> effective plan
  -> native graph
  -> real trial invocation
  -> trace and closure snapshot
  -> expectation/control/quality assessment
  -> versioned campaign artifact
```

Those fixtures may use deterministic model drivers where the target supports
them, but they may not substitute a second hand-authored agent registry or a
prebuilt trace for native execution.

## Deferred Follow-Ups

These do not block the initial local, side-effect-denied implementation:

- Add a concrete sandbox provider that can enforce filesystem, network,
  process, and secret isolation beyond `InProcessEnvironment`.
- Define additional acknowledgements, budgets, and concurrency controls before
  enabling `explicit_live` side effects. Provider-backed models can ship
  earlier with explicit model selection, denied side effects, and reported
  cost/data posture.
- Add graph-reuse attestations only after an adapter can prove stateless native
  graph reuse. Fresh trial systems remain the default.
- Add an append-only ledger or alternate artifact store behind the versioned
  directory-manifest contract when operational demand justifies it.

## Decision Summary

Proceed with a cross-target materialized eval runner, but do not implement the
older OpenAI-first proposal verbatim.

The essential promise is:

> Contract4Agents applies the selected eval overlay before planning, invokes
> the reviewed native graph through a target adapter, captures one
> identity-bound evidence frontier, and assesses it without exposing
> evaluator-only truth or silently reaching production systems.

For host-owned workflows, Contract4Agents assesses finalized workflow and trace
evidence; it does not become the workflow engine.

If the selected target, model source, environment, isolation provider, or
evidence path cannot meet that promise, planning must say so before a trial
runs.
