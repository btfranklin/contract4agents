# Open Questions

Resolved decisions belong in the relevant topical document, not a historical
decision ledger.

The accepted ownership and assurance decisions are specified in
[`semantic-model.md`](../architecture/semantic-model.md).

Record new questions here only when implementation cannot proceed without a
real product choice. Include the affected lifecycle stage, competing options,
required evidence, and the decision owner.

## Native Eval Public Release

The [native eval proposal](../../NATIVE_EVAL_EXECUTION.md) starts with an internal
Strands experiment. The following choices block the public interface, but do
not block that experiment. B.T. owns the product decisions; implementation
results should supply the evidence for each choice.

| Stage | Choice | Evidence needed |
| --- | --- | --- |
| Fixture resolution | Keep fixture references in the existing expression representation or introduce typed AST/IR values; use a file source alone or add a Python source | A working typed fixture example, including trial-scoped host context, that shows the minimum required representation and access path |
| Native invocation | Select the first supported provider model and adapter after the scripted Strands experiment | An opt-in native invocation with correct input, context, output, trace completion, and documented cost and data handling |
| Assessment and reporting | Extend the existing campaign result or add a separate result type only if required | Concrete success, invalid-output, exception, and incomplete-trace results; a report disclosure review; evidence that execution and assessment outcomes remain distinct |
| Campaign execution | Continue independent trials after an ordinary failure or stop at the first failure | The example's expected user workflow and tests that distinguish an ordinary trial failure from invalid campaign configuration |

Approval execution, judge configuration, operational metrics, assurance
ingestion, baseline comparison, and a richer persisted artifact format are
deferred. Open a focused decision when one is selected for implementation.
Do not make their design a prerequisite for the direct invocation experiment.
