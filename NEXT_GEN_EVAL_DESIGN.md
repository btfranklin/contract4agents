# Next-Generation Evaluation Design

**Status:** remaining-work proposal for a materialized direct-entry assurance
runner.

The deterministic replay foundation is implemented. This document now defines
only the remaining native evaluation path, the invariants that path must
preserve, and the evidence gates for expanding it.

## Product Decision

Contract4Agents should add a built-in path that invokes one declared entry agent
from the reviewed native graph and assesses the resulting contract-bound
evidence.

That path closes the remaining product gap. Contract4Agents already declares
eval cases, plans and materializes native graphs, captures normalized traces,
and assesses controls. `eval replay` honestly assesses prerecorded evidence but
does not invoke a graph.

The initial implementation should therefore deliver this spine:

```text
portable contract and eval case
  -> typed fixture resolution
  -> complete eval-specific target bindings
  -> normal materialization plan and native graph
  -> one direct entry-agent invocation
  -> immutable trace and closure snapshot
  -> shared expectation and control assessment
  -> small versioned result artifact
```

It should not make that value contingent on first building:

- a durable campaign job system;
- resumable or idempotent campaign execution;
- a second target-configuration control plane;
- a generalized baseline-policy engine;
- a whole-trial sandbox product;
- operational-control aggregation;
- approval execution before exact causal correlation exists; or
- simultaneous execution support for every target adapter.

The larger ideas remain legitimate future directions. They are not one
indivisible feature and must earn their place through concrete use.

## Current Implementation Boundary

The public `eval replay` command performs deterministic replay assessment. Its
file provider resolves invocation, host-context, evaluator-truth, and redacted
report channels separately, then loads prewritten outputs, normalized trace
events, closure evidence, approval decisions, judge decisions, and metrics from
schema-version `1` `eval-data.json`.

Replay acquisition produces `FinalizedTrialEvidence`. The provider-free
`assess_finalized_evidence(...)` boundary performs deterministic, control,
quality, and trace assessment. Result serialization exports an invocation
digest and explicit redacted `report`, not raw invocation, host context,
evaluator truth, or a generic `inputs` field.

It does **not**:

- call `materialize(...)`;
- construct a native graph for the campaign;
- invoke an agent or model;
- execute a tool or datasource;
- generate trace evidence from execution; or
- prove that the selected target behaves like the replay fixture.

Replay assessment supports deterministic examples, imported production
evidence, application-owned execution, and assurance without model or provider
access. It remains supported under the explicit `replay` name.

The new path is `run`: it materializes and invokes a native direct-entry agent.

## Product and Architecture Boundaries

The implementation must preserve the accepted Contract4Agents ownership model.

### The contract remains the product

`.contract` and `.eval` source own portable semantics:

- agents, instructions, guidance, and composition;
- typed inputs and outputs;
- capability interfaces and grants;
- controls, qualities, and operational controls;
- eval scenarios and deterministic expectations; and
- expected evidence.

An eval-specific binding source may choose implementations. It may not redeclare
or weaken portable semantics.

### The host owns deterministic workflow

The built-in runner handles one direct entry-agent invocation. It does not infer
or execute:

- run specs;
- deterministic stage ordering;
- branches or loops;
- application retries;
- transactions;
- persistence or recovery;
- workflow checkpoints; or
- application terminal-selection policy.

An application that needs to evaluate an outer workflow continues to own that
workflow. It may supply finalized execution and run-spec evidence through an
advanced integration seam.

### Materialization remains target-native

The runner uses the ordinary native graph produced by `materialize(...)`.
OpenAI Agents SDK, Strands Agents, and Google ADK keep their native object and
invocation models. Contract4Agents standardizes only the narrow request,
identity, evidence, and assessment boundaries needed for a direct eval trial.

### One normal materialization plan governs the graph and evidence

The first implementation does not introduce an
`EvalMaterializationPlan` parallel to `MaterializationPlan`.

The selected eval bindings produce one ordinary, authoritative
`MaterializationPlan`. Its digest governs:

- graph validation;
- invocation identity;
- normalized trace events;
- trace closure;
- deterministic and control assessment; and
- the eval result artifact.

No runner may build a production graph and patch tools, models, instructions, or
grants afterward.

### The runner owns logical invocation lifecycle

`TraceAttempt` is a host-owned concept. For built-in direct trials, the runner is
the host. It owns:

- campaign-execution and trial-execution IDs;
- invocation and attempt IDs;
- creation and binding of `TraceAttempt`;
- terminal-attempt selection;
- separation of execution and assessment status; and
- final artifact assembly.

Target adapters own native invocation mechanics and provider correlation. They
do not independently invent retry or terminal-selection semantics.

### Assessment remains evidence, not enforcement

Contracts, traces, approvals, judges, and assurance results do not enforce
business authorization, transactions, production idempotency, or organizational
policy. Application code retains those responsibilities.

## Design Goals

1. Add an honest `eval run` command and Python API that invoke a reviewed native
   direct-entry graph.
2. Reuse normal compilation, planning, materialization, tracing, closure, and
   assessment rather than building an eval-specific agent registry.
3. Resolve `.eval` fixture references into validated entry-agent input while
   preserving the existing audience-separated trial-data boundary.
4. Require complete eval-specific dependency bindings and prohibit implicit
   fallback to another binding source.
5. Use fresh mutable runtime and trace state for every trial.
6. Keep execution outcome separate from behavioral assessment outcome.
7. Produce a small, versioned, identity-validated native result.
8. Prove the product path on one adapter before expanding it.

## Non-Goals for the Initial Native Release

- Executing arbitrary customer workflows.
- Providing a hosted eval service or background campaign scheduler.
- Resuming a process after a crash.
- Reusing completed trials through idempotency keys.
- Running trials concurrently.
- Reusing a mutable native graph across trials.
- Supporting approval-required capability execution.
- Permitting declared side effects.
- Providing configurable quality judges.
- Assessing operational controls.
- Comparing native runs against configurable baselines.
- Ingesting native eval artifacts into assurance bundles before a dedicated
  validator exists.
- Enforcing filesystem, network, process, or secret isolation in-process.
- Shipping native execution for all three adapters simultaneously.

These are deliberate boundaries, not accidental omissions.

## Remaining Public Product Shape

The new `run` operation sits beside the implemented `eval replay` command and
does not change replay semantics.

### Run a native direct-entry eval

The initial supported shape is intentionally narrow:

```bash
contract4agents eval run agent_contracts \
  --target strands \
  --profile test \
  --bindings contract4agents.eval.targets.toml \
  --fixtures eval-fixtures.json \
  --trials 1 \
  --out .contract/eval-run
```

`eval run`:

- requires an explicit eval-specific binding source;
- resolves typed fixtures;
- materializes a fresh native system for each trial;
- invokes the eval case's declared entry agent;
- captures one trace and closure frontier;
- assesses deterministic expectations and supported controls; and
- writes a versioned result artifact.

## Initial Supported Slice

The first shippable slice supports:

- the Strands adapter;
- a deterministic scripted model selected through normal target bindings;
- one direct entry agent per eval case;
- one attempt per trial;
- sequential trials;
- trusted local fixture and dependency implementations;
- `in_process` execution with no sandbox claim;
- only reachable capabilities declared `side_effect = false`;
- no reachable `approval_required` grants;
- typed input and output validation;
- normalized tracing and closure;
- deterministic eval expectations;
- shared behavioral control assessment;
- missing quality-judge evidence becoming `unverified`;
- a small versioned result artifact.

The runner must reject an initial-slice campaign before native invocation when:

- the selected adapter lacks a direct-trial executor;
- the model configuration is not a supported scripted source;
- a fixture cannot be resolved or validated;
- a declared external boundary lacks an eval binding;
- a reachable capability is side-effecting;
- a reachable grant requires approval;
- the entry agent requires an unsupported invocation shape;
- a required isolation guarantee exceeds `in_process` enforcement;
- required trace capture cannot be established; or
- the plan contains a required degraded or unsupported mapping.

Starting this narrowly is important. It lets the implementation prove the
contract-to-execution-to-assurance path without pretending that approvals,
provider-backed models, stronger isolation, or host workflows are solved.

## Eval-Specific Bindings

### First implementation: select one complete binding source

The first native slice should use the existing `bindings=` materialization seam
with a complete, explicit eval-specific target-binding file.

For example:

```toml
schema_version = "1"

[targets.strands]
adapter = "strands"

[targets.strands.tools."research.fetch"]
python = "acme_agent.evals.tools:fetch_fixture"

[targets.strands.datasources."research.current"]
python = "acme_agent.evals.context:current_fixture"

[targets.strands.profiles.test]
default_model = "scripted"

[targets.strands.profiles.test.options]
model_factory = "acme_agent.evals.models:scripted_model"
```

The file must be complete for the selected target and profile. There is no
fallback to `contract4agents.targets.toml`, no profile inheritance, and no
post-plan replacement.

This approach deliberately accepts some duplication of implementation locators
while the product shape is being proven. It avoids immediately introducing:

- base-versus-overlay precedence;
- a second environment schema;
- effective-binding merge diagnostics;
- an eval-plan digest distinct from the materialization-plan digest;
- eval-plan visualization; or
- semantic diff for an unvalidated configuration model.

### Possible follow-up: narrow `EvalBindings`

A typed overlay may become worthwhile when complete eval binding files prove
meaningfully repetitive or error-prone.

If introduced, it must:

- address existing semantic IDs only;
- replace implementation locators and model-driver choices only;
- supply fixture and optional judge hooks;
- never change agents, prompts, topology, grants, controls, types, output
  schemas, or isolation declarations;
- apply before implementation import and planning;
- produce one effective `TargetBindings` value;
- produce one normal `MaterializationPlan`; and
- fail if any external boundary remains unresolved.

It should not become a general configuration language for measurement,
retention, release policy, concurrency, storage, or sandbox orchestration.

Whether this overlay is needed is an explicit investigation item, not an
assumption embedded in the first release.

## Planning and Materialization Order

The initial runner follows one order:

```text
load contract and eval source
  -> load the selected complete eval binding source
  -> validate target-binding conformance
  -> resolve and type-check trial fixtures
  -> compute the ordinary materialization plan
  -> verify initial-slice restrictions
  -> import only implementations named by that plan
  -> build and validate the native graph
  -> bind execution and trace identity to the plan digest
```

Fixture validation may inspect canonical contract types before materialization,
but project implementation imports occur only after binding and plan validation.

The graph, trace, assessments, and result artifact must all identify the same
plan digest.

## Typed Native Fixture Resolution

Replay already establishes four typed audience channels: invocation, host
fixture context, evaluator truth, and redacted report view. Native execution
must preserve that boundary. The remaining work is to resolve typed fixture
references into the invocation and host-context channels without exposing
evaluator truth.

`TypeName.fixture("name")` resolves through a typed fixture source. The resolver
must:

1. parse the fixture reference as a language construct rather than preserve it
   as an arbitrary string;
2. load the named value without exposing evaluator truth;
3. validate it against the named canonical contract type;
4. map it to the selected entry agent's declared input shape; and
5. fail before invocation on missing, extra, or invalid values.

The first implementation should prefer a deterministic file-backed fixture
source. A Python `FixtureSource` protocol may follow when examples demonstrate a
need for computed or database-backed fixtures.

An illustrative initial file shape is:

```json
{
  "schema_version": "1",
  "dataset": {
    "id": "market-research-native-eval",
    "version": "2026-07-30"
  },
  "fixtures": {
    "type:MarketResearchQuestion": {
      "field_ops_ai": {
        "product_area": "AI operations software",
        "target_segment": "field-service teams",
        "decision": "identify the strongest initial market opportunity",
        "as_of_date": "2026-07-30"
      }
    }
  },
  "cases": {
    "eval:MarketResearchLead:validates_segment_opportunity": {
      "host_context": {},
      "evaluator_truth": {
        "market_thesis": "The expected fixture-only conclusion."
      }
    }
  }
}
```

The entire canonical fixture source, including evaluator truth, contributes to
one source digest so truth changes cannot reuse a campaign definition or
assessment identity. The artifact records that digest and the dataset
identities, not the raw truth. If ordinary hashing would disclose a low-entropy
sensitive value, a future restricted-data posture must define a keyed digest or
opaque dataset-version authority before such data is supported.

`evaluator_truth` is loaded into its dedicated channel, never merged with the
referenced fixture value. The initial schema should omit arbitrary hook
locators, judge configuration, measurement policy, and persistence settings.

Deterministic tool, datasource, and external-context implementations remain
selected by the eval target bindings. If they need trial-scoped data beyond the
entry input, the Strands spike must establish the smallest explicit
`HostFixtureContext` access mechanism; ambient global mutation is not an
acceptable public design.

## Direct Trial Runner

The native runner must emit the implemented `FinalizedTrialEvidence` model and
call the implemented provider-free `assess_finalized_evidence(...)` function.
It must not reproduce replay scoring logic. Campaign aggregation—trial counts,
rates, uncertainty intervals, and simple thresholds—may remain shared after
trial assessment. Baseline comparison remains disabled for the initial native
path.

The public Python API should remain small:

```python
runner = materialize_eval_runner(
    "agent_contracts",
    target="strands",
    profile="test",
    bindings="contract4agents.eval.targets.toml",
    fixtures="eval-fixtures.json",
)

artifact = await runner.run(
    trials=1,
    out=".contract/eval-run",
)
```

Final naming should be settled during implementation, but the API should expose
one runner rather than a hierarchy of plan, factory, mutable system, and runner
objects.

### Runner responsibilities

For every trial, the runner:

1. generates a new trial-execution ID;
2. resolves typed trial data;
3. materializes a fresh system;
4. generates one invocation and initial-attempt identity;
5. opens the adapter's trace session;
6. binds the host-owned `TraceAttempt`;
7. invokes the selected entry agent through the adapter executor;
8. records terminal-attempt selection;
9. waits for the adapter's supported instrumentation-quiescence boundary;
10. closes the trace session to an immutable snapshot;
11. validates output against the canonical contract type and validates trace,
    closure, and plan identity;
12. assesses supported expectations and controls;
13. derives execution and assessment status separately; and
14. contributes one terminal trial result to the artifact.

The first slice performs no retry. A failed attempt is terminal.

### Adapter executor responsibilities

The adapter-specific executor owns only what cannot be portable:

- locating the native entry agent;
- rendering validated invocation values into the SDK's accepted input form;
- invoking the native SDK;
- extracting and translating the native structured output without deciding
  canonical conformance;
- correlating provider-native request, run, trace, and span identifiers;
- reporting provider-supported metrics;
- establishing or declining instrumentation quiescence; and
- returning explicit unsupported or degraded facts.

It does not:

- choose eval cases;
- resolve evaluator truth;
- create campaign or trial identity;
- create retry chains;
- choose terminal attempts;
- assess contract controls; or
- persist campaign state.

### Cross-target protocol

The public request and evidence values should be portable, but the protocol must
be informed by a real Strands implementation before it is frozen for other
adapters.

An illustrative boundary is:

```python
class DirectTrialExecutor(Protocol):
    async def invoke(
        self,
        request: NativeTrialInvocation,
    ) -> NativeTrialResult: ...
```

`NativeTrialInvocation` contains the materialized system, selected entry-agent
ID, validated invocation values, and bound trace-session services. It does not
contain evaluator truth or persistence services.

`NativeTrialResult` contains the extracted terminal output, provider
correlation, available metrics, quiescence evidence, and diagnostics. The
runner—not the adapter—validates the canonical output type and owns the final
normalized trace/closure snapshot and assessment.

The exact protocol is an investigation item until the Strands spike proves the
minimum common surface.

## Direct Agent Runs and Host-Owned Workflows

The built-in executor invokes one declared entry agent. Composition available
to that agent remains part of the reviewed native graph and may run through the
provider's supported delegation or handoff mechanism.

The runner does not automatically execute a `run_spec` or infer a deterministic
application workflow from composition.

An application-owned workflow can participate later through an advanced
executor that:

- calls materialized agents from host code;
- owns branching, retry, persistence, recovery, and terminal selection;
- uses existing trace-session and `TraceAttempt` primitives;
- closes the session to an immutable snapshot; and
- supplies separate `RunSpecEvidence` when a run spec applies.

That seam is not part of the initial direct-runner implementation.

## Safety Model

### Trusted in-process execution

The first release runs trusted project code in the current Python process.
It must report `in_process` and must not claim filesystem, network, process, or
secret isolation.

Selecting a complete eval binding source can guarantee that every
Contract4Agents-declared external boundary resolves through that source. It
cannot guarantee that arbitrary imported Python code will not:

- read environment variables;
- access the filesystem;
- open a network connection;
- spawn a process; or
- perform an undeclared side effect.

The correct promise is:

> The runner seals resolution of Contract4Agents-declared dependencies to the
> selected eval bindings and executes trusted local code in-process.

It is not:

> The runner proves that local code cannot reach production systems.

### Side effects

The initial runner rejects any reachable capability declared
`side_effect = true`.

That static rule is a product boundary, not a security sandbox. A capability
declared side-effect-free is still trusted application code. Documentation must
state that incorrect declarations or malicious fixture implementations are
outside the in-process enforcement boundary.

### Production fallback

The selected eval binding source is complete and exclusive. Missing bindings
fail planning. The runner never falls back to:

- the default target-binding file;
- another target or profile;
- production model settings;
- an ambient provider;
- an unselected implementation locator; or
- live side-effecting behavior.

Ambient environment variables remain visible to trusted in-process code unless
a future isolation provider prevents that.

### Provider-backed models

The first native slice uses a scripted model. Provider-backed models may follow
with explicit:

- model selection;
- credential and data-handling documentation;
- cost posture;
- retention posture;
- side-effect restrictions; and
- user acknowledgement.

Provider-backed models do not imply production tools or production data.

## Trial Identity and Status

### Identities

The initial artifact records:

- `campaign_label`: an optional human-readable label, not a durable identity;
- `campaign_definition_digest`: a canonical digest of selected cases, trial
  count, fixture and evaluator-dataset identities, and relevant runner settings;
- `campaign_execution_id`: a generated ID for one invocation of the runner;
- `case_id`: the canonical eval semantic ID;
- `trial_execution_id`: a generated ID for one trial;
- `invocation_id`: the logical direct-agent invocation;
- `attempt_id`: the one host-owned attempt; and
- the contract and materialization-plan digests.

The design must not treat an arbitrary user-supplied campaign name as sufficient
identity for resume, baseline lookup, or artifact association.

### Execution status

Initial execution states are:

```text
planned -> running -> succeeded
                   -> failed
                   -> invalid_evidence
```

Assessment remains:

```text
passed | violated | unverified
```

Examples:

- `succeeded/passed`: invocation and required assessment succeeded.
- `succeeded/violated`: execution completed and evidence disproved a
  requirement.
- `succeeded/unverified`: execution completed but required evidence was missing.
- `failed/unverified`: native invocation failed without evidence proving a
  violation.
- `invalid_evidence/unverified`: identity, trace, attempt, or closure evidence
  is contradictory and cannot enter assurance.

Timeout and user-driven cancellation are deferred until adapter behavior and
CLI semantics are investigated. A process interrupt may terminate the command,
but the first artifact contract does not promise terminal closure, an artifact,
or resumable cancellation after interruption.

A future `timed_out` status is valid only when the adapter can prove native task
termination and instrumentation quiescence. Stopping the await while provider
work or trace hooks continue is not a terminal timeout and must abort the
campaign without publishing finalized trial evidence.

## Trace Ownership and Closure

The runner owns one disposable trace session per trial.

The sequence is:

```text
open session
  -> bind host-owned attempt
  -> invoke through adapter
  -> record terminal selection
  -> establish adapter-supported quiescence
  -> close to immutable trace and closure
  -> validate
  -> assess
```

An arbitrary delay or no-op flush is not closure evidence. If an adapter cannot
establish a complete instrumentation frontier, closure is incomplete and
absence-dependent claims remain `unverified`.

The direct runner uses `trial_execution_id` as normalized trace `run_id`. The
trace binds the exact contract and materialization-plan digests. Provider-native
IDs remain correlation fields rather than portable lifecycle authority.

Exceptions preserve whatever incomplete trace evidence can be closed honestly.
They never convert missing events into proof. Timeout evidence follows the
stricter rule above: no final closure unless native termination and quiescence
are established.

## Assessment Semantics

Native and replay paths converge only after each has produced finalized,
identity-valid evidence.

The initial native sequence is:

1. validate terminal output shape;
2. validate trace conformance and plan identity;
3. validate the one-attempt lifecycle and terminal selection;
4. validate trace closure;
5. evaluate deterministic eval expectations;
6. call the existing shared behavioral-control assessor;
7. represent quality expectations without a configured judge as `unverified`;
8. derive behavioral assessment separately from execution status; and
9. write the result artifact.

The same shared control assessor must interpret native eval traces and imported
production traces.

### Quality judges

Configurable judges are deferred from the initial native slice. The first
runner must not silently skip a declared quality expectation or treat its
absence as success. It records an `unverified` quality result with an explicit
reason.

Judge support may follow after the team decides:

- the callable or provider interface;
- audience-safe judge inputs;
- prompt and policy provenance;
- model, version, cost, and data-handling identity;
- evaluator-truth disclosure rules; and
- failure and retry semantics.

### Operational controls

Operational-control assessment is not required to prove native direct
execution. It remains a separate design effort because latency, cost, tokens,
retries, volume, and cross-run expressions require:

- adapter-attested measurement semantics;
- clear units and aggregation rules;
- missing-measurement behavior;
- per-trial versus campaign scope; and
- separation from release thresholds.

Unsupported operational controls remain visible and `unverified`; they are not
silently ignored.

## Approval-Required Capabilities

The initial native runner rejects reachable `approval_required` grants.

Approval support cannot be added by correlating only capability identity and
timestamp. A future approval request must bind:

- campaign and trial execution IDs;
- invocation and attempt IDs;
- agent, grant, and capability IDs;
- provider tool-call or request ID;
- canonical argument digest;
- approval-policy identity and version or digest;
- issuance time and optional expiry; and
- immutable evidence references.

An allow decision must repeat the request digest. A tool start satisfies the
approval-required control only when it matches that exact unexpired decision in
the same invocation and attempt.

The runner still would not become the application's production authorization
system. Eval approval would govern trusted test doubles and produce assessment
evidence.

## Result Artifact

The initial runner writes a small versioned directory artifact under one unique
campaign-execution ID:

```text
eval-run/
  <campaign-execution-id>/
    manifest.json
    trials/
      <trial-execution-id>.json
    traces/
      <trial-execution-id>.jsonl
    closures/
      <trial-execution-id>.json
```

This is a deterministic output layout, not a storage service or resumable job
ledger.

`manifest.json` version `1` records:

- package version and artifact schema version;
- campaign definition digest and execution ID;
- selected case IDs and trial count;
- contract and materialization-plan digests;
- target, profile, adapter, and scripted-model identity;
- eval binding-source digest;
- invocation-fixture and evaluator-dataset identities;
- a source digest that binds the exact evaluator truth without serializing it;
- execution posture: native, scripted, trusted local, side effects denied, and
  in-process;
- per-trial execution and assessment status;
- output, trace, closure, assessment, and diagnostic digests;
- campaign summary; and
- completion status.

Trial result files contain redacted invocation/report views, terminal output,
metrics, expectation results, control results, quality results, and diagnostics.
Evaluator truth is excluded.

The runner stages the entire execution directory in a unique sibling temporary
directory. It writes and durably closes every referenced artifact first, writes
the complete manifest last, then renames the staged directory to the final
previously unused campaign-execution path on the same filesystem. That directory
rename is the commit point. A staging directory left by a crash is incomplete
and must never be discovered as a completed artifact.

The first release does not replace an existing execution directory and does not
promise:

- transition-by-transition persistence;
- process-crash recovery;
- resume;
- idempotent trial reuse;
- an append-only ledger;
- retention management; or
- alternate artifact stores.

### Assurance ingestion

Native eval output must not enter an assurance bundle as arbitrary JSON.
Assurance ingestion follows only after a validator checks:

- supported schema version;
- complete artifact state;
- contract and plan identities;
- campaign and trial identities;
- referenced artifact digests;
- trace and closure validity; and
- report redaction.

Until then, the native artifact is an inspectable eval result, not verified
assurance-bundle evidence.

## Baseline Comparison

Baseline comparison is deferred from the initial native runner.

The current aggregate-only baseline shape is not sufficient for native evidence.
A future comparison must establish comparability across at least:

- target and adapter;
- model source and model identity;
- materialization-plan inputs;
- eval bindings and dependency implementations;
- fixture dataset and trial selection;
- judge and approval policies when present;
- measurement and aggregation rules; and
- redaction policy when reports are compared.

The initial artifact records these identities where they exist so future
comparability is possible. It does not implement a named policy language or
apply regression tolerances.

When baseline support is revisited, the simplest safe rule should be tested
first: exact environment identity or `incomparable`. Configurable policies that
allow selected differences require demonstrated release-comparison use cases.

## Failure Semantics

| Condition | Execution status | Assessment consequence |
| --- | --- | --- |
| Eval bindings are incomplete or incompatible | campaign does not start | no trial assessment |
| Fixture input is missing or invalid | trial does not start | explicit diagnostic |
| Reachable side effect or approval requirement is present | campaign does not start | unsupported initial slice |
| Required isolation exceeds in-process support | campaign does not start | no trial assessment |
| Native invocation and evidence complete | `succeeded` | assess normally |
| Required expectation or control is disproven | `succeeded` | `violated` |
| Native provider, tool, or context execution fails | `failed` | `unverified` unless captured evidence proves a violation |
| Trace identity, attempt, or closure is contradictory | `invalid_evidence` | fail closed |
| Required negative assertion lacks complete closure | unchanged | assertion is `unverified` |
| Quality expectation has no supported judge | unchanged | quality result is `unverified` |

A normal trial failure does not create recovery or retry behavior in the first
slice. The runner may continue with independent remaining trials according to a
simple fail-fast option, but it does not resume after process termination.

## Specific Recommended Roadmap

This is the recommended implementation order. It intentionally proves user
value before building platform machinery.

### Phase 1: prove one native Strands spike

Build an internal, deliberately narrow vertical slice using:

- one purpose-built or verified public example with only side-effect-free,
  preapproved capabilities;
- one complete eval-specific target-binding file;
- one deterministic file-backed fixture source;
- a scripted Strands model;
- one direct entry agent;
- one attempt;
- one fresh materialization;
- existing Strands tracing and closure;
- deterministic expectations and shared controls; and
- an in-memory result.

The spike must prove:

```text
contract
  -> typed fixture
  -> normal plan
  -> native Strands graph
  -> actual invocation
  -> real normalized trace and closure
  -> assessment
```

Exit criteria:

- no `FileEvalProvider` supplies output or trace evidence;
- no second agent registry or hand-authored trace exists;
- one plan digest joins graph, trace, closure, and result;
- an intentionally introduced behavior failure is detected;
- evaluator truth cannot influence invocation; and
- the adapter/runner ownership boundary is small enough to document.

Do not freeze a cross-target executor API until this spike is reviewed.
If no current public example meets the initial restrictions, add a small
purpose-built example. Do not weaken the safety semantics of an existing example
merely to make it eligible.

### Phase 2: ship the initial `eval run`

Turn the reviewed spike into the supported initial product:

- public Python runner API;
- `eval run` CLI;
- fresh state per trial;
- generated campaign, trial, invocation, and attempt identity;
- separate execution and assessment status;
- minimal versioned artifact and validator;
- no timeout option unless the Strands spike proves native termination and
  instrumentation quiescence;
- explicit unsupported diagnostics for approvals, side effects, stronger
  isolation, provider-backed models, host workflows, and unavailable metrics;
- one complete public example; and
- acceptance tests for the entire path.

Exit criteria:

- a user can run a declared eval without supplying output or trace evidence;
- the path is deterministic offline;
- no production binding fallback occurs;
- all negative claims depend on valid closure;
- failed and unverified outcomes remain distinct; and
- the artifact can be independently revalidated against its files and digests.

### Phase 3: expand only along demonstrated seams

Candidate work, each as a separate decision:

1. Add a narrow `EvalBindings` overlay if complete binding files create
   demonstrated duplication or drift.
2. Add Google ADK direct execution after the Strands protocol has proved
   portable without hiding meaningful SDK differences.
3. Add OpenAI provider-backed execution with explicit cost, credential, data,
   and retention posture; do not fake scripted-model parity.
4. Add audience-safe quality judges with policy provenance.
5. Add exact causal approval support, then enable approval-required test
   capabilities.
6. Add validated assurance-bundle ingestion.
7. Add operational-control assessment one metric family at a time.

Every item requires its own acceptance tests and may ship independently.

### Phase 4: operational features only when usage justifies them

Consider these only after real native campaigns demonstrate the need:

- parallel trial execution;
- durable transition persistence;
- cancellation across provider SDKs;
- crash recovery and resume;
- idempotency keys and completed-trial reuse;
- configurable baseline comparability policies;
- graph-reuse attestations;
- append-only ledgers or alternate artifact stores;
- retention management; and
- subprocess or container sandbox providers.

Evidence for taking on this work should include campaign duration, CI failure
modes, artifact volume, operator needs, and concrete user workflows. “A mature
eval platform might need it” is not sufficient evidence.

## Validation Requirements

### Native spike and initial run

Tests must prove:

- typed fixture parsing and validation against canonical types;
- entry-agent input-shape validation;
- exclusive selection of the eval binding source;
- failure on every missing declared dependency;
- failure on reachable side effects and approval-required grants;
- honest rejection of unsupported isolation;
- one materialization-plan digest across graph, trace, closure, and artifact;
- actual native Strands invocation;
- fresh context runtime, graph, trace session, and identity per trial;
- one host-owned attempt and terminal selection;
- output-schema validation;
- complete and incomplete closure behavior;
- negative expectations after success and failure;
- distinct execution and assessment statuses;
- missing quality judge becoming `unverified`;
- redacted result serialization;
- artifact digest validation; and
- no hand-authored output or trace in the acceptance fixture.

### Expansion work

Every added adapter must have:

- a real native-object construction test;
- a deterministic offline direct-invocation test where the SDK supports it;
- an opt-in provider-backed test where required;
- trace correlation and closure tests;
- exception behavior tests and deadline tests only when the adapter claims
  terminal timeout support; and
- explicit unsupported/degraded capability tests.

Approval, judge, assurance, operational-control, baseline, persistence, and
sandbox work each require focused validation of their own threat and failure
models. They do not inherit credibility merely because direct execution works.

## Areas Requiring Further Investigation or Discussion

This section is a phase-local research and discussion agenda, not a second
decision ledger. Before a phase begins, any item that has become a genuine
implementation-blocking product or architecture choice must be promoted to
`docs/decisions/open-questions.md`. The accepted answer then belongs in the
relevant topical documentation and should replace the question here.

Questions that do not block the current phase should not delay it.

### Investigate before the Strands spike

1. **Fixture syntax ownership:** Should fixture references become typed AST/IR
   nodes for the native runner, or can a narrowly validated fixture-expression
   value preserve format version `1` during product v0?
2. **Native input rendering:** What is the smallest deterministic rendering of
   typed invocation data and resolved context that Strands accepts without
   inventing a second prompt system?
3. **Scripted model contract:** Which existing Strands `model_factory` behavior
   is stable enough to make the public example deterministic?
4. **Trace ownership:** Can the runner open and close the existing Strands
   session while the adapter executor handles invocation without exposing
   provider-specific session types publicly?
5. **Instrumentation quiescence:** Which Strands lifecycle event proves that
   provider hooks have finished emitting evidence?
6. **Termination and timeout eligibility:** Does cancellation of
   `invoke_async(...)` stop native work and hooks, or only stop awaiting it?
   Unless termination and quiescence can be established, the initial runner
   must expose no terminal timeout behavior.
7. **Example selection:** Which public example has enough composition and trace
   behavior to prove the feature while containing no reachable side effects or
   approvals? Current examples may not qualify; should the spike add a smaller
   purpose-built example instead?

### Investigate before public `eval run`

1. **Fixture source:** Is a JSON fixture file sufficient for the first public
   release, or is a trusted Python fixture protocol required for realistic
   context and datasource setup?
2. **Artifact location and redaction:** Which invocation and output fields are
   safe in default artifacts, and how does a user request more restricted
   output?
3. **Failure continuation:** Should the default continue independent trials
   after an ordinary failure, or fail fast? Either choice remains in-process
   and non-resumable.
4. **Deadline CLI:** If the Strands spike proves terminal timeout semantics,
   is one per-trial deadline sufficient or is a campaign wall-clock deadline
   also necessary? If it does not, the initial CLI has no deadline option.
5. **Campaign definition digest:** Which runner settings are semantic inputs to
   the digest, and which are output or presentation choices?
6. **Package/API naming:** Should the Python entry point be
   `materialize_eval_runner(...)`, `create_eval_runner(...)`, or an explicit
   method on an existing materialization service?

### Non-blocking follow-up questions

1. **Narrow overlay demand:** How much duplication do complete eval binding
   files create in public and user projects?
2. **Adapter order:** Does user demand justify Google ADK or OpenAI next?
3. **OpenAI execution posture:** Should provider-backed OpenAI evals ship before
   a deterministic model seam exists?
4. **Quality judging:** Should judge policy be a Python protocol, provider
   binding, authored prompt asset, or combination?
5. **Approval UX:** Which adapter-native interrupt and confirmation models can
   preserve exact request causality?
6. **Assurance ingestion:** Should native eval artifacts be embedded, copied, or
   referenced by assurance bundles?
7. **Operational controls:** Which first metric has reliable cross-adapter
   semantics: latency, tokens, cost, or retries?
8. **Baseline use case:** Are users comparing contracts, plans, models,
   fixtures, providers, or releases? Comparability policy depends on the
   intended experiment.
9. **Durability demand:** How long and expensive must campaigns become before
   resume and completed-trial reuse justify a persistent lifecycle?
10. **Isolation provider:** Is a subprocess boundary sufficient for the first
    stronger-isolation implementation, or do target use cases require a
    container or remote sandbox?

Technical spike results belong in the relevant topical architecture, eval,
runtime, trace, or assurance documentation. Product and architecture choices
follow the canonical open-question process described above.

## Deferred Work and Evidence Gates

| Candidate | Why deferred | Evidence required to reconsider |
| --- | --- | --- |
| Eval-binding overlay | Avoid a second configuration authority before duplication is measured | Repeated complete binding files cause demonstrated drift or user burden |
| Google ADK parity | Avoid freezing an unproven cross-target protocol | Strands executor boundary is stable and ADK use is requested |
| OpenAI native eval | No equivalent scripted-model seam; provider runs add cost/data concerns | Explicit provider-backed use case and accepted posture |
| Approval execution | Current broad correlation is not causally sufficient | Exact request/attempt/tool-call design and tests |
| Quality judge | Requires audience, prompt, policy, cost, and failure decisions | Concrete quality-eval workflow |
| Operational controls | Metrics do not yet have complete aggregation semantics | One reliable metric and user requirement |
| Assurance ingestion | Arbitrary JSON must not count as verified evidence | Versioned artifact validator |
| Baseline policy | Comparability use case is not yet known | Repeated native campaigns and a named comparison question |
| Resume/idempotency | Creates durable job ownership | Long or expensive campaigns with observed restart pain |
| Concurrency | Adds shared-state and rate-limit complexity | Measured runtime problem |
| Graph reuse | Mutable context/runtime state makes reuse unsafe by default | Adapter attestation and performance need |
| Strong sandboxing | Cannot be implemented by in-process assertions | Defined threat model and enforceable provider boundary |

## Decision Summary

Proceed with a materialized direct-entry assurance runner.

The essential first-release promise is:

> Contract4Agents resolves typed eval fixtures, selects one complete
> eval-specific binding source, builds the ordinary reviewed native graph,
> invokes one declared entry agent through a supported adapter, captures one
> identity-bound trace and closure frontier, and assesses that evidence without
> exposing evaluator-only truth.

For host-owned workflows, Contract4Agents continues to assess supplied workflow
and trace evidence; it does not become the workflow engine.

For in-process execution, Contract4Agents seals only its declared dependency
resolution; it does not claim an operating-system security boundary.

The recommended implementation order is:

```text
one real Strands vertical slice
  -> small supported eval run
  -> evidence-led adapter and assurance expansion
  -> operational platform features only when usage demands them
```

That sequence completes the product's contract-to-evidence loop without turning
Contract4Agents into a general eval service before the direct runner has proved
its value.
