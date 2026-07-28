# Next-Generation Evaluation Design

**Status:** proposal for follow-up design and implementation work.

## Thesis

Contract4Agents should materialize evaluations in the same way it materializes
agent graphs.

Today the public `eval` command is a deterministic evidence-fixture assessor:
it loads `FileEvalProvider`, which reads prewritten inputs, outputs, normalized
trace events, closure evidence, approval decisions, and quality decisions from
`eval-data.json`. `run_campaign(...)` then validates and assesses that supplied
evidence. It does **not** invoke a materialized agent or model.

That is a useful replay/fixture capability, but it does not meet the intuitive
meaning of “run an eval.” A user who has configured an agent target should not
have to implement the difficult orchestration seam merely to execute their
contract's `.eval` cases against the configured agent.

The next-generation design should provide this user experience:

```python
runner = materialize_eval_runner(
    "agent_contracts",
    target="openai",
    profile="test",
    environment="local_fixtures",
)

report = await runner.run(trials=3)
```

```bash
contract4agents eval agent_contracts \
  --target openai \
  --profile test \
  --environment local_fixtures \
  --trials 3
```

In this design, every trial is a real invocation of the selected target's
materialized agent graph. Contract4Agents owns the generic plumbing; users
provide only their application-specific fixtures, test doubles, approval
decisions, and optional judge policy.

This remains a contract-and-assurance system, not a hosted eval service or a
general workflow language.

## Why This Matters

The current system's strongest value is not generic trace bucketing. It is the
chain from declared intent to a reviewed plan to observed evidence:

```text
portable contract
  -> target/profile materialization plan
  -> materialized agent graph
  -> normalized, identity-bound trace and closure
  -> shared assessment and campaign result
```

That chain lets reviewers ask questions a generic observability pipeline cannot
answer without rebuilding the same contract model:

- Did a release add a capability, weaken authorization, or widen context?
- Did the target actually implement the control as native, host-enforced,
  emulated, degraded, or unsupported?
- Did the observed tool call belong to the exact reviewed contract and plan?
- Can an absence assertion be proven from complete closure, rather than from a
  missing log line?
- Do controlled evals and imported production traces use the same control
  semantics?

However, that value is incomplete when the built-in eval path does not produce
the execution evidence it assesses. The missing live runner is therefore a
product coherence issue, not merely a convenience feature.

## Design Goals

1. **One command that actually executes the configured agent.** A configured
   target, test profile, and eval environment should be enough to run canonical
   `.eval` cases without users implementing `EvalProvider.execute`.
2. **Reuse normal materialization.** The eval graph must be the same target,
   profile, instructions, output types, grants, tool wiring, and plan used by
   the ordinary runtime path.
3. **Isolate only irreducibly application-specific concerns.** Users should
   supply test data and test doubles, not campaign orchestration, trace
   normalization, closure, assessment, or report aggregation.
4. **Fail closed.** Missing fixture bindings, context, approval policy, model
   driver, required telemetry, or judge evidence must be explicit failures or
   `unverified` results. Never silently fall back to production dependencies.
5. **Keep source authority clean.** `.contract` and `.eval` retain portable
   semantics. Target bindings select target-specific implementations. Eval
   environments select target-specific test implementations and must not repeat
   agent prompts, permissions, controls, schemas, or topology.
6. **Preserve replay.** Pre-recorded evidence remains valuable for regression,
   incident replay, and deterministic unit-level coverage. It must be named and
   documented honestly as replay/fixture assessment.
7. **Provide a stable cross-target shape.** The first implementation can be
   OpenAI Agents SDK-specific, but the public abstraction must allow future
   targets to provide their own native runner adapter.

## Non-Goals

- Running arbitrary customer application workflows without any application
  integration point.
- Inventing fake EHR, billing, CRM, payment, or other domain logic.
- Treating a semantic judge as an access control or business-policy engine.
- Running production credentials, production write tools, or real sensitive
  data by default.
- Hiding target/provider differences behind a misleading “universal agent
  runtime.”
- Replacing the host's deterministic workflow, transactional policy logic,
  persistence, retries, or recovery decisions.

## Core Product Shape

### Materialized evaluation result

Add a public materialization entry point, with final naming to be decided:

```python
eval_runner = materialize_eval_runner(
    root,
    *,
    target: str,
    profile: str,
    environment: str,
    bindings: TargetBindings | Path | str | None = None,
)
```

It should return an immutable result roughly like:

```python
@dataclass(frozen=True)
class EvalMaterializationResult:
    system: MaterializationResult
    eval_plan: EvalMaterializationPlan
    runner: MaterializedEvalRunner
```

`system` is the existing materialized native graph. `eval_plan` binds the base
materialization plan to the selected eval environment and records exact
implementations, caveats, and host obligations. `runner` executes `.eval`
cases and returns the existing `CampaignResult` model (extended only where the
new execution evidence requires it).

The central invariant is:

> A materialized eval runner may not construct a second, hand-maintained agent
> registry or tool graph.

It consumes the same `MaterializationResult` that ordinary runtime execution
uses.

### Trial lifecycle

For every canonical eval case and trial index:

```text
1. Resolve typed case fixtures and evaluator-only hidden truth.
2. Create a sealed eval invocation context.
3. Resolve only eval-environment tool, datasource, and external-context bindings.
4. Materialize or reuse the reviewed native graph.
5. Invoke the target's native entry-agent runner.
6. Apply the selected approval policy when a capability requests approval.
7. Capture native spans/events through the target trace adapter.
8. Snapshot the trace and exact closure frontier at terminal completion.
9. Validate output and trace conformance against the contract and reviewed plan.
10. Evaluate deterministic expectations and shared controls.
11. Call configured quality judges for declared quality expectations.
12. Produce one `TrialResult`, then aggregate the campaign report.
```

The report's output, trace, metrics, closure evidence, control results, and
judge provenance must all belong to the same trial, contract digest, plan
digest, and eval-environment digest.

## Eval Environments

### Why a new configuration concept is necessary

Current target profiles select model identifiers and provider options. Current
target bindings select one target-wide implementation per tool, datasource, and
external context. That is insufficient for a safe real eval runner: a test run
must be able to replace production integrations with narrow test doubles and
fixture providers without changing portable contract semantics.

Add a target-specific **eval environment**. It is an implementation overlay,
not a second contract or a profile inheritance mechanism.

Recommended conceptual TOML shape:

```toml
[targets.openai.eval_environments.local_fixtures]
adapter = "openai"
fixture_source = "acme_agent.evals.fixtures:source"
tool_overrides = "acme_agent.evals.tools:overrides"
datasource_overrides = "acme_agent.evals.context:datasource_overrides"
external_context_overrides = "acme_agent.evals.context:external_overrides"
approval_policy = "acme_agent.evals.approvals:policy"
judge = "acme_agent.evals.judges:quality_judge"
environment = "contract4agents.runtime:InProcessEnvironment"
```

The final schema should use explicit, inspectable per-semantic-ID mappings
rather than an opaque catch-all object if doing so improves validation and plan
review. The shape above illustrates responsibilities, not final syntax.

The environment must not declare or override contract-owned fields such as
agent permissions, authorization, controls, quality rubrics, output schemas,
guidance, composition, or isolation requirements. Existing target-binding
authority checks should extend to enforce this.

### Sealed-default behavior

An eval environment should be sealed by default:

- unresolved tool, datasource, or external-context dependency fails;
- production environment variables and write-capable implementations are not
  inherited implicitly;
- no network access exists unless the selected environment explicitly provides
  and proves it;
- side-effecting capabilities are denied unless the environment supplies an
  explicit safe double or authorized sandbox implementation;
- missing approval policy is a denial or an explicit `unverified` outcome,
  never automatic approval;
- missing quality judge leaves a quality result `unverified`.

This prevents the most dangerous failure mode: a developer intends to run a
test and quietly invokes production systems.

### Environment variants

The design should support at least three explicit modes. They are distinct
products, not aliases for one another.

| Mode | What executes | Primary use |
| --- | --- | --- |
| `replay` | No agent; supplied output/trace evidence | Deterministic regression, incident replay, assessor tests |
| `materialized` | Real target-native agent graph with local/sandboxed dependencies | Default integration evals |
| `live` | Real target-native agent graph and a deliberately configured live model | Model regression, latency/cost, controlled release gates |

`replay` preserves the current `FileEvalProvider` behavior. `materialized` is
the new default user story. `live` may share the same implementation but must
make provider, cost, data, and side-effect posture explicit in the plan and
report.

## User-Supplied Narrow Interfaces

The runner should not require users to write a general `EvalProvider`. It
should provide small, domain-appropriate hooks instead.

### Fixture source

The fixture source resolves `TypeName.fixture("name")` and named values in an
eval case. It returns data that the runner validates against canonical contract
types. Hidden truth is evaluator-only and must never become model-visible
context.

### Test doubles and sandbox bindings

Users provide ordinary implementations for external boundaries that cannot be
invented by the framework:

- host tools;
- datasources;
- external context;
- remote services; and
- possibly a controlled sandbox endpoint.

The runner resolves and validates these using the same target-binding and
callable-shape mechanisms used for normal materialization. The only difference
is the selected eval environment supplies the implementation overlay.

### Approval policy

The approval policy receives a typed capability request plus scenario/trial
identity and returns an allow/deny decision with a reason and evidence
reference. A deterministic policy can approve or deny configured cases. A
live/sandbox policy can route to a test approval service. It must not silently
approve every side effect.

### Quality judge

Quality remains an evaluator concern. The runner passes the declared rubric,
output, and safe trace view to a configured judge. The judge returns passed or
violated status, reason, score if applicable, provider/version, and evidence
references.

Judge prompts and policy should live in external versioned Markdown or another
reviewable asset, not inline in Python. The report must retain the judge
identity, version, prompt/policy digest, and data-handling posture. The runner
must not send evaluator-only hidden truth or sensitive fields to a judge unless
the selected environment explicitly authorizes that boundary.

### Optional host-workflow entry point

Direct agent-entry invocation is sufficient for the common case. Some
applications own deterministic orchestration around materialized agents. For
those, support a narrow optional host entry-point binding that receives the
materialized system and typed eval invocation context, then returns the
selected terminal result and trace session.

This is not a workflow DSL. It is the explicit escape hatch for an application
that must exercise its real deterministic workflow rather than call one entry
agent directly.

## Target Adapter Responsibilities

The first implementation is an OpenAI Agents SDK eval runner. It should:

1. reuse the existing OpenAI materialization provider and native agent graph;
2. invoke the native SDK runner for the entry agent;
3. bind declared host tools to eval-environment implementations;
4. route approval interruptions through the eval approval policy;
5. install the existing normalized trace router/processor;
6. open one trace session and one attempt identity per trial;
7. snapshot the trace and closure from the same terminal session frontier;
8. collect output, latency, cost, and token metrics when available; and
9. emit explicit evidence when any target capability is unsupported or
   degraded.

Future targets implement the same small adapter contract. They may differ in
how they invoke a native agent, intercept approval, inject context, or capture
traces; the resulting `EvalExecution` and campaign report remain portable.

The existing generic `EvalProvider` protocol should become an advanced adapter
seam behind this materialized runner. It remains useful for unusual remote,
replay, or organization-specific execution, but ordinary adopters should not
need to implement it.

## Plan and Evidence Model

### Eval materialization plan

The existing `MaterializationPlan` identifies the contract, target, profile,
bindings, mapping outcomes, expected events, and host obligations. Eval
execution introduces additional material facts, so it needs a companion plan
or an explicit extension with a distinct digest.

The eval plan should record:

- base contract and materialization-plan digests;
- target, profile, and eval-environment identity/digest;
- fixture-source identity/version;
- every tool, datasource, and external-context implementation selected for
  testing;
- model driver/provider identity and options;
- approval-policy identity/version;
- judge identity/version/prompt-policy digest;
- environment enforcement evidence and permitted side effects/network posture;
- expected trace channels and event types; and
- any unsupported, degraded, or host-enforced mappings.

An eval result is only comparable to a baseline when the relevant contract,
plan, eval-environment, judge, and measurement settings are known. The report
must make drift visible rather than treating all trials under the same case name
as equivalent.

### Trace and closure

The runner must own trace lifecycle in materialized mode. It should not accept a
best-effort trace dumped later by the host as sufficient negative evidence.

For each trial, it must capture a normalized trace plus identity-bound closure
evidence at one exact frontier. This is what makes expectations such as
`trace.not_called(...)` meaningful. If instrumentation is incomplete, the
result is `unverified`, even if the output looks correct.

Trace exports and judge inputs need audience-specific redaction. Sensitive
domain fixtures must not leak into generic logs, report artifacts, or external
judges merely because a test is running.

## CLI and API Surface

The final public API should be small and direct.

### Materialized evaluation

```bash
contract4agents eval agent_contracts \
  --target openai \
  --profile test \
  --environment local_fixtures \
  --trials 3 \
  --out .contract/eval-results.json
```

Expected behavior:

- materialize and validate the eval graph before starting trials;
- fail before execution if the eval plan is unsupported, degraded when required,
  or resolves an unsafe dependency;
- execute real trials;
- write one report with trial evidence, metrics, and comparable digests;
- exit nonzero for violations, unverified required results, or failed campaign
  thresholds.

### Explicit replay

The current fixture behavior should become explicit in the CLI and docs, for
example:

```bash
contract4agents eval replay agent_contracts \
  --target openai \
  --profile test \
  --data eval-data.json
```

The exact command name is open, but the distinction is not: replay assessment
must not be represented as execution. Because this repository rejects
compatibility shims, choose a clear final command structure and migrate docs,
tests, and examples together rather than retaining ambiguous aliases.

### Python

```python
runner = materialize_eval_runner(
    "agent_contracts",
    target="openai",
    profile="test",
    environment="local_fixtures",
)

report = await runner.run(
    trials=3,
    thresholds=CampaignThresholds(min_pass_rate=0.95),
)
```

Keep lower-level functions available for adapters and tests, but make this the
normal documented path.

## Failure Semantics

The runner must distinguish failures accurately:

| Condition | Trial outcome |
| --- | --- |
| Model returns a conforming output but violates a required expectation/control | `violated` |
| Approval is denied and the contract expects no action | potentially `passed`, depending on expectations/control applicability |
| Model/provider/tool/judge/fixture failure | `unverified` unless supplied evidence proves a violation |
| Missing or incomplete trace closure for a negative assertion | `unverified` |
| Required plan mapping unsupported or unsafe eval environment | fail before campaign execution |
| Test double attempts a disallowed side effect | explicit violation/failure with trace evidence |

This preserves the existing asymmetric evidence rule: absence only proves a
negative claim when the relevant channel is demonstrably closed.

## Security and Safety Defaults

This feature is particularly important for the business-policy and healthcare
safety patterns in the documentation. It must not create a path that claims to
test safety while quietly reaching production systems.

Required defaults and checks include:

- test environments are explicit, named, and digested;
- all external boundaries resolve through the selected environment;
- test fixtures are validated and audience-classified;
- side-effecting capabilities require an explicit sandbox implementation or
  are denied;
- no production secrets, external context, or persistence are inherited by
  convention;
- live-model use is explicit and reports model, cost, and data posture;
- quality judges receive only a permitted redacted audience view;
- raw sensitive inputs are not copied into output reports by default; and
- users can assemble an assurance bundle from a live eval report, but the
  bundle remains evidence, not compliance certification.

## Migration From Current Behavior

1. Preserve the current file-backed implementation as a first-class replay
   engine with truthful naming and documentation.
2. Add eval-environment models, parsing, validation, serialization, and plan
   digesting without changing contract source authority.
3. Implement the OpenAI materialized runner behind the new public API.
4. Convert one existing public example to materialized local fixtures, while
   retaining a replay fixture for deterministic assessor coverage.
5. Change the public `eval` command to the materialized execution story and
   move old file semantics to an explicit replay command as part of a deliberate
   pre-1.0 migration.
6. Update README, language reference, CLI reference, quality guidance, example
   docs, and all tests so no page implies that replay executes an agent.

No compatibility alias should preserve the ambiguous meaning of “run eval.”

## Implementation Tranches

### Tranche 1: semantics and plans

- Define `EvalEnvironment` and `EvalMaterializationPlan` models.
- Extend target-binding schema, loader, validation, serialization, and
  conformance checks.
- Prove eval environments cannot duplicate portable contract authority.
- Add plan visualization and semantic-diff coverage for environment changes.
- Add exact digest rules and fail-closed validation.

### Tranche 2: materialized OpenAI execution

- Introduce the eval-runner materialization entry point.
- Reuse the existing OpenAI graph/materialization path.
- Add typed fixture resolution, tool/context override resolution, and approval
  interception.
- Integrate the normalized trace router with exact snapshot/closure capture.
- Produce `EvalExecution` directly from live native execution.

### Tranche 3: judging and reports

- Add a reviewable judge binding with external prompt/policy assets.
- Redact judge inputs according to audience and environment rules.
- Extend campaign report and assurance bundle data with eval-environment and
  judge provenance.
- Add cost, latency, token, and provider-error coverage from real trials.

### Tranche 4: replay migration and documentation

- Rename/reposition `FileEvalProvider` behavior as replay.
- Update CLI semantics and examples.
- Add a complete local-fixture, materialized-eval tutorial.
- Update the business-policy, healthcare, and vendor/payment guides to show
  actual execution, not only evidence fixtures.

### Tranche 5: hardening and additional targets

- Add concurrency, retry, cancellation, timeout, and crash-recovery tests.
- Verify side-effect denial and production-fallback prevention.
- Add a remote/application-entrypoint integration path.
- Implement the same public runner contract for additional provider targets.

## Validation Requirements

The implementation should add tests for:

- fixture types, hidden-truth audience separation, and invalid fixture failure;
- complete replacement of declared dependencies by sealed eval environments;
- refusal to invoke production bindings when an eval environment is selected;
- approval allow, deny, missing, and out-of-order behavior;
- actual OpenAI agent invocation with local fake tools;
- normalized trace correlation, closure, and absence assertions;
- output, control, quality, and provider-failure result semantics;
- redaction before judge/export;
- plan/environment/judge digest changes and baseline comparability;
- concurrency and idempotency behavior for side-effecting test doubles; and
- replay behavior after its explicit migration.

At least one public example must prove the full path:

```text
contract -> plan -> materialized eval runner -> real trial -> normalized trace
-> closure -> expectation/control/quality assessment -> campaign report
```

That fixture must not use a second hand-authored agent registry or prebuilt
trace as a substitute for the materialized trial.

## Open Design Questions

1. **Configuration shape:** should eval environments be target-level entries,
   profile-owned entries, or separate binding files selected by CLI/API? The
   recommended direction is target-level, named entries selected alongside a
   complete profile, because it keeps model selection separate from dependency
   replacement.
2. **Model drivers:** how should deterministic scripted model behavior coexist
   with real-provider live runs? A scripted driver is useful for integration
   tests but must not be mislabeled as model-behavior evaluation.
3. **Side effects:** should materialized eval environments categorically forbid
   contract-declared side-effecting tools unless they resolve to a marked
   sandbox, or permit explicitly approved test stores? The safe default should
   be denial; the exact capability marker needs design.
4. **Host workflows:** what is the smallest interface that lets applications
   evaluate their deterministic outer workflow without turning Contract4Agents
   into a workflow engine?
5. **Quality judges:** should the first release support only an injected Python
   judge callable, or ship a target-backed judge adapter with a strongly
   reviewable external prompt asset?
6. **Trial isolation:** when may a graph be reused across trials, and which
   state/context/session objects must be recreated every time? Defaulting to
   fresh trial state is safest.
7. **Cost control:** what explicit budgets, concurrency limits, and live-model
   acknowledgements should be required before a CLI starts an expensive live
   campaign?
8. **Baseline policy:** which digests must match for a baseline comparison to
   be meaningful, and which changes should create an explicit incomparable
   result rather than a regression verdict?

## Decision Summary

Proceed with a materialized eval runner. Treat it as a first-class sibling of
normal agent materialization, not as a custom-provider convenience layer.

The essential promise should be:

> Configure the narrow domain dependencies that only the application can know.
> Contract4Agents materializes and executes the reviewed agent graph, captures
> the evidence, and assesses the result.

If that promise cannot be met safely for a selected target/environment, planning
must say so before a trial runs.
