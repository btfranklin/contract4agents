# Run Specs

Run specs describe expected observable behavior for a host-owned multi-agent
run. They verify stage outputs, trace ordering, tool constraints, and run-level
invariants without defining executable workflow control.

The host application still owns ordering, branching, retries, checkpointing,
recovery, persistence, and business logic.

## Source Syntax

```contract
run_spec CompendiumResearch:
    stages = [
        plan: PlannerAgent -> ResearchPlan,
        section_research+: SectionResearchAgent -> SectionResearchBrief,
        verification?: VerifierAgent -> VerificationReport,
        synthesis: SynthesisAgent -> CompendiumPayload,
    ]

    assertions = [
        expect(trace.called_before(PlannerAgent, SectionResearchAgent)),
        expect(trace.max_calls(VerifierAgent, 2)),
        expect(trace.not_tool_called_by(SynthesisAgent, openai.web_search)),
        expect(value.synthesis_citation_ids subset_of value.ledger_cited_ids),
    ]
```

Stage suffixes define output cardinality:

- no suffix: exactly one output is required.
- `?`: the stage is optional.
- `+`: one or more outputs are required.

Run spec assertions can use trace expressions over the normalized trace and
derived-value data relations supplied by the host after the run. Use stage
outputs for schema checks, trace assertions for ordering and capability-use
expectations, and derived values for cross-stage data invariants.

Derived-value assertions use host-supplied `value.<name>` references and set
operators:

```contract
expect(value.synthesis_citation_ids subset_of value.ledger_cited_ids)
expect(value.ledger_cited_ids contains_all value.synthesis_citation_ids)
expect(value.left equals_set value.right)
expect(value.left intersects value.right)
expect(value.left disjoint_from value.right)
```

Derived values are intentionally not a transformation language. If the invariant
needs filtering or flattening, such as "ledger entries where status is cited,"
compute that in host Python and pass the resulting scalar sequence to
`evaluate_run_spec(...)`.

## Compiler Artifact

The compiler emits `run-specs/run-specs.json`. Each run spec records:

- run spec name and source path
- stage name, agent, output type, cardinality, manifest reference, and schema reference
- assertion text

The artifact is included in compile freshness checks, so `compile --check`
reports stale or missing run spec output.

## Runtime Evaluation

Host applications evaluate compiled run specs after a run:

```python
from contract4agents.assertions import evaluate_run_spec

result = evaluate_run_spec(
    contract=artifacts,
    run_spec="CompendiumResearch",
    trace=trace,
    stage_outputs={
        "plan": plan_output,
        "section_research": section_outputs,
        "synthesis": synthesis_output,
    },
    derived_values={
        "ledger_cited_ids": source_ledger.cited_ids,
        "synthesis_citation_ids": payload.citation_ids,
    },
    run_id="run-123",
)
```

`stage_outputs` is keyed by stage name. Repeated `+` stages expect a non-empty
sequence of outputs; required and optional single stages expect one output
object when present. Each output is validated against the declared stage output
schema.

`derived_values` is keyed by the name used after `value.` in assertion text.
Values may be scalars or sequences of scalars. Strings are treated as scalar
items, not character sequences. Unknown derived values, missing derived values,
non-scalar items, and unsupported operators fail closed.

The first supported cross-stage data invariant pattern is derived-value based.
Direct nested stage projections such as `stage.synthesis.sections[]` and filters
such as `where status == "cited"` are outside this surface; host Python should
prepare those values before calling `evaluate_run_spec(...)`.

Single-run traces can omit `run_id`. Multi-run traces must pass `run_id` so
events from separate runs cannot satisfy the same run spec.
