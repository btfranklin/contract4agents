# Eval Language Reference

`.eval` files declare scenarios against canonical agent IDs. The selected
target plan supplies the runtime inventory and expected event types; eval source
contains portable scenario givens and outcome expectations.

```contract
eval answers_from_current_evidence for ResearchLead:
    given question = ResearchQuestion.fixture("current_market")
    expect output conforms ResearchBrief
    expect trace.tool_called(current_facts.fetch)
    expect trace.agent_called(CurrentTruthScout)
    expect trace.not_called(status.publish)
    expect output discovers hidden_truth.market_driver
    expect quality(evidence_backed)
```

## Givens

`given <name> = <value>` supplies a portable scenario value. The built-in
`FileEvalProvider` combines literal givens with the case and trial `invocation`
objects from `eval-data.json`. Custom providers resolve the same typed
invocation boundary. Typed `TypeName.fixture("name")` resolution is reserved
for the future native runner and is not performed by replay.

Replay data keeps host fixture context, evaluator truth, and redacted report
data in separate `host_context`, `evaluator_truth`, and `report` objects.
Evaluator truth must not enter invocation input, model instructions, ordinary
runtime context, judge requests, or default report output.

## Deterministic Expectations

Supported output expectations include:

- `output conforms TypeName`
- `output.field == value`
- `output.field != value`
- `output.field contains value`
- `output.field excludes value`
- `output discovers hidden_truth.field_name`

Supported trace expectations include:

- `trace.called(name)` and `trace.not_called(name)`
- `trace.called_once(name)` and `trace.called_times(name, n)`
- `trace.called_before(a, b)` and `trace.called_after(a, b)`
- `trace.max_calls(name, n)`
- `trace.tool_called(capability.name)`
- `trace.agent_called(AgentName)`
- `trace.datasource_resolved(datasource.name)`
- `trace.approval_requested(capability.name)`
- `trace.approval_granted(capability.name)`
- `trace.approval_denied(capability.name)`
- `trace.contains("text")`

Expressions are resolved against canonical semantic IDs and normalized trace
schema. Unsupported expressions fail closed during semantic analysis or
produce an explicit unverified result if unchecked input reaches an assessor.

## Negative Claims

`trace.not_called(...)` can pass only when identity-bound trace closure covers
the relevant instrumentation channel for every observed attempt. Event-family
occurrence alone is not proof. An absent event without closure produces
`unverified`, not `passed`.

## Quality Expectations

`expect quality(name)` references a named `quality` declaration for the eval's
agent. The eval provider supplies a `JudgeDecision` containing:

- passed or violated status;
- reason and optional score;
- judge provider and version;
- evidence references.

Judge absence, errors, malformed output, or missing provenance produce an
unverified quality result. Quality rubrics are evaluator/reviewer-visible by
default and do not enter the model prompt.

## Hidden Truth

The `evaluator_truth` object may contain scalar hidden-truth values or explicit
matcher objects:

```json
{"contains_all": ["rollback", "checkout-api"]}
```

```json
{"contains_any": ["revert", "disable"]}
```

The hidden-truth loader and assessor are evaluation concerns. The provider
passes this object only through the typed evaluator channel; hidden values are
structurally unavailable to replay execution and omitted from ordinary report
serialization.

## Campaign Results

Each trial finishes as `passed`, `violated`, or `unverified`. Replay reports
separate deterministic expectations, contract control results, quality results,
trace evidence, and provider failures. They expose an invocation digest and an
explicit redacted `report` projection, never generic raw `inputs`. Repeated
campaigns add rates, uncertainty intervals, latency/cost/token summaries,
threshold checks, and baseline comparisons.

## Run-Spec Relations

Run specs use the same expression parser for trace relations and additionally
support host-supplied derived-value relations:

- `value.left subset_of value.right`
- `value.left contains_all value.right`
- `value.left equals_set value.right`
- `value.left intersects value.right`
- `value.left disjoint_from value.right`

These relations validate deterministic host workflow after execution. They are
not valid inside agent guidance, controls, or normal eval expectations.
