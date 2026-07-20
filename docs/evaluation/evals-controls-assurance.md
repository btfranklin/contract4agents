# Evals, Controls, and Assurance

Contract4Agents evaluates declared intent against observed evidence. The same
stable contract identities and control assessor are used for controlled eval
runs and imported production traces.

## Three Different Concerns

- A **control** is a required or advisory behavioral requirement with a named
  assessment mode and expected evidence.
- An **assessment** compares available evidence with one or more controls.
- An **assessor** is the implementation or named identity that performs an
  assessment.
- A **quality** declaration is a qualitative rubric assessed by a named judge
  or reviewer.
- An **operational control** is a latency, cost, retry, volume, or cross-run
  concern that is not derivable from a behavioral requirement.

These are not interchangeable. Model guidance is not an enforced control, and
a missing judge result is not a passing quality result.

```contract
control evidence_before_publish for IncidentCommander:
    severity = high
    required = true
    audience = [host, evaluator, reviewer]
    assessment = post_run
    when = trace.tool_called(status.publish)
    require = trace.agent_called(LogInvestigator)
    expected_evidence = [approval.completed, composition.completed]

quality operational_summary for IncidentCommander:
    rubric = "The summary is concise, evidence-backed, and operationally useful."
    audience = [evaluator, reviewer]

operational_control latency for IncidentCommander:
    severity = medium
    require = trace.duration < 30s
```

Approval-required grants and typed agent outputs create derived controls. They
must not be re-declared as separate behavioral assessment rules.

## Result Semantics

Every assurance result is one of:

- `passed`: sufficient evidence demonstrates the requirement.
- `violated`: sufficient evidence demonstrates the requirement failed.
- `unverified`: the evidence is missing, incomplete, malformed, or unable to
  establish the claim.

The distinction is deliberately asymmetric. Positive events may prove a
positive claim. Event absence proves a negative claim only when identity-bound
closure proves the relevant instrumentation channel closed at that trace frontier.

Conditional controls evaluate `when` before `require`. A proven-false
condition produces a passed result with `applicability = "not_applicable"`; the
requirement is not evaluated. A proven-true condition makes the requirement
applicable. An unverifiable condition produces an unverified result with
`applicability = "unverified"`. Absence can prove a condition false only when
the relevant trace channel has complete closure evidence.

## Eval Scenarios

`.eval` files declare cases against named agents:

```contract
eval identifies_checkout_cause for IncidentCommander:
    given request = IncidentReportRequest.fixture("checkout_latency")
    expect output conforms IncidentBrief
    expect trace.tool_called(logs.search)
    expect trace.agent_called(LogInvestigator)
    expect trace.not_called(status.publish)
    expect quality(operational_summary)
```

The compiler derives the runtime inventory from the canonical IR and the
materialization plan. The eval case does not repeat agent factories, tool
registries, permissions, output types, or expected agent counts.

## Campaign Execution

The public eval path selects the same target and profile used for planning:

```bash
contract4agents eval agent_contracts --target openai --profile test
```

An eval provider supplies scenario inputs, external context, datasource/tool
behavior, approval decisions, execution, and semantic judge decisions. The
built-in file provider reads schema-version `2` `eval-data.json` for
deterministic offline runs. Each trial includes an explicit closure declaration;
the provider binds that declaration to the attempts and events it constructs.
Custom providers implement the `EvalProvider` protocol for live, replayed, or
application-integrated execution. `EvalExecution` returns both a normalized
trace and its `TraceClosureEvidence`; a custom provider cannot authorize
absence-dependent results with event-family occurrence alone.

Campaigns support repeated trials, pass/violation/unverified rates, Wilson
uncertainty intervals, latency and cost summaries, thresholds, and baseline
comparisons. Provider and judge failures produce unverified trials rather than
disappearing or becoming passes.

## Shared Assessment

`assess_controls(ir, plan, trace, closure=trace_closure)` is the common
provider-neutral assessor. It
uses the plan's requested mechanisms and expected event types when evaluating
contract controls. Eval campaigns and production trace assessment call this
same API. Both paths first validate trace conformance against the canonical
contract and reviewed plan; invalid digests, undeclared capabilities, and
missing or contradictory tool/grant identities are rejected before scoring.

Continuous monitoring is an operational pattern outside the contract language:
a scheduler, trace pipeline, or observability service repeatedly invokes the
assessor as complete traces arrive. Contract4Agents performs assessments; it
does not itself watch a live system.

This prevents the offline and production interpretations of a control from
drifting apart.

Run specs have a separate post-run assessor:

```python
assess_run_spec(
    ir,
    plan,
    trace,
    "ResearchRun",
    run_spec_evidence,
    closure=trace_closure,
)
```

The host still executes stages, computes derived values, and decides when its
workflow is terminal. `RunSpecEvidence` supplies typed stage observations and
an explicit complete, incomplete, or unverified workflow-evidence status.
`assess_run_spec(...)` validates cardinality, agent and output-type conformance,
derived values, and declared assertions. It does not execute stages or make
retry and recovery decisions. A passing control result is not a passing
run-spec result, and the two result types remain distinct.

Trace closure is also distinct from host retry semantics. Closure establishes
which instrumentation paths were captured at one exact trace frontier; an
`attempt.selected` event establishes which attempt the host chose as terminal.
A fully captured retry chain can therefore have complete closure while output
assurance remains unverified because no terminal attempt was selected.

## Assurance Bundles

An assurance bundle is a deterministic evidence package containing:

- canonical contract IR and contract digest;
- materialization plan and plan digest;
- normalized trace JSONL;
- versioned, identity-bound trace-closure evidence;
- control results whose reasons and evidence reflect trace evidence;
- run-spec results for contracts whose selected workflow is declared by a run
  spec;
- eval campaign summaries when available;
- semantic contract or plan diffs when available;
- explicit diagnostics for absent or inconsistent evidence.

Bundle verification checks internal digest references and records missing
evidence. A bundle is review evidence, not a claim that a legal or regulatory
standard has been certified.

`assess_assurance_evidence(...)` is the library-level orchestration entry point.
It accepts raw normalized trace, closure-manifest, and run-spec manifest objects,
computes control and run-spec results, and delegates deterministic packaging to
`assemble_assurance_bundle(...)`. Call the lower-level assembler only when the
application already owns those assessed result objects.

Run-spec bundle input includes explicit `RunSpecSelection` evidence for every
run. A selection may name one declared run spec or state that none applied.
Results are accepted only when their contract, plan, run, and run-spec identity
matches that selection; their canonical evidence digest binds the exact stage
outputs and derived values that were assessed.

Controls and run specs remain different claims. A control assesses one
behavioral requirement and may be conditional. A run spec assesses a
host-selected workflow declaration: stage identity, cardinality, typed outputs,
derived values, and assertions. Neither result substitutes for the other.

## Semantic Diffs

Structural text diffs are insufficient for high-reliability agent changes.
Contract4Agents semantic diffs identify changes in:

- capability access and authorization;
- schemas and context exposure;
- isolation requirements;
- control requiredness, audience, and assessment;
- model selection and enforcement outcomes when the Python API receives both
  previous and candidate plans;
- quality and eval coverage.

Reviewers can therefore distinguish a harmless wording edit from expanded tool
access, weakened approval, degraded isolation, or lost evidence coverage.

## Audience Safety

Controls and quality rubrics have explicit audiences. Evaluator-only criteria,
fraud thresholds, hidden trip conditions, and reviewer material are not inserted
into model instructions unless their audience explicitly includes `model`.
Trace exports apply audience-specific redaction before events leave the process.
