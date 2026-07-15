# Eval Language Reference

`.eval` files declare scenarios against canonical agent IDs. The selected
target plan supplies the runtime inventory and expected telemetry; eval source
contains only case inputs and outcome expectations.

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

`given <name> = <value>` supplies an agent input or named eval-provider value.
`TypeName.fixture("name")` requests a provider-owned fixture whose result must
conform to the named contract type. `hidden_truth` values are evaluator-only
and must not enter model instructions or ordinary runtime context.

The built-in `FileEvalProvider` resolves these values from `eval-data.json`.
Other `EvalProvider` implementations may supply live application inputs,
replayed data, external context, datasources, approvals, and judge decisions.

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

`trace.not_called(...)` can pass only when trace completeness proves the
relevant instrumentation covered the complete run. An absent event in an
incomplete trace produces `unverified`, not `passed`.

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

Hidden truth may use scalar values or explicit matcher objects in deterministic
eval data:

```json
{"contains_all": ["rollback", "checkout-api"]}
```

```json
{"contains_any": ["revert", "disable"]}
```

The hidden-truth loader and assessor are evaluation concerns. Hidden values
must be omitted from runtime inputs and audience views that include the model.

## Campaign Results

Each trial finishes as `passed`, `violated`, or `unverified`. Campaign reports
separate deterministic expectations, contract control results, quality results,
trace completeness, and provider failures. Repeated campaigns add rates,
uncertainty intervals, latency/cost/token summaries, threshold checks, and
baseline comparisons.

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
