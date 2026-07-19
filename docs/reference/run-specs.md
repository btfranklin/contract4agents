# Run Specs

Run specs declare verifiable expectations for deterministic workflow that the
host application owns. They do not execute stages, branch, retry, loop, or
transform data.

```contract
run_spec ResearchRun:
    stages = [
        evidence: EvidenceAgent -> EvidenceMap,
        specialist+: SpecialistAgent -> SpecialistAssessment,
        review?: ReviewerAgent -> ReviewResult,
        synthesis: ResearchLead -> ResearchBrief,
    ]

    derived_values = [
        cited_ids: list[string],
        allowed_ids: list[string],
    ]

    assertions = [
        expect(trace.called_before(EvidenceAgent, ResearchLead)),
        expect(trace.max_calls(ReviewerAgent, 2)),
        expect(value.cited_ids subset_of value.allowed_ids),
    ]
```

## Stage Cardinality

- no suffix: exactly one stage result;
- `?`: zero or one result;
- `+`: one or more results.

Every stage names its expected agent and portable output type. Semantic
analysis rejects unknown agents/types and output types that do not match the
agent signature. The stage list declares identities and cardinalities; list
position is not an implicit serial-execution requirement. Use an explicit
trace assertion when order matters.

## Derived Values

Derived values are host-computed scalar facts used for post-run invariants.
Portable declarations support `string`, `integer`, `float`, `boolean`, and
`list[...]` of those scalar types.

Supported set relations are:

- `subset_of`
- `contains_all`
- `equals_set`
- `intersects`
- `disjoint_from`

Filtering, flattening, projections, and business transformations remain normal
host code. The host records the prepared values and provenance used for the
assessment. Every `value.*` name used by an assertion must be declared in the
run spec's `derived_values` block.

## Trace Relations

Run specs use normalized traces and therefore inherit contract/plan identity,
semantic IDs, parent relationships, and completeness rules. A missing event can
prove a negative or ordering claim only when the reviewed plan establishes the
necessary telemetry coverage. Otherwise the result is unverified.

## IR and Assurance

Run specs are stored in canonical IR with stable IDs and included in the
contract digest. Canonical IR version 3 retains the derived-value declarations
as well as stage and assertion declarations.

After a host workflow finishes, assess one selected declaration with the
separate `assess_run_spec(...)` API:

```python
from contract4agents.assurance import RunSpecEvidence, RunSpecStageObservation, assess_run_spec
from contract4agents.ir import FrozenMap, semantic_id

evidence = RunSpecEvidence(
    status="complete",
    reason="The host workflow ledger is closed.",
    stage_observations=(
        RunSpecStageObservation(
            observation_id="evidence-1",
            stage="evidence",
            agent_id=semantic_id("agent", "EvidenceAgent"),
            output={"items": []},
            evidence_event_ids=("evt-evidence-completed",),
        ),
    ),
    derived_values=FrozenMap(
        {"cited_ids": ("source-1",), "allowed_ids": ("source-1", "source-2")}
    ),
    evidence_refs=("workflow-ledger:run-123",),
)
result = assess_run_spec(ir, plan, trace, "ResearchRun", evidence)
```

`RunSpecEvidence.status` is `complete`, `incomplete`, or `unverified`. A
`complete` claim requires a host-owned completeness evidence reference. Each
stage observation likewise links to a normalized trace event or an immutable
evidence artifact. Trace-backed observations require a linked event with the
claimed agent identity. The assessor does not infer workflow completion from
the absence of more trace events.

Results are `passed`, `violated`, or `unverified`:

- a complete run with a missing required stage, wrong cardinality, wrong agent,
  invalid output schema, invalid derived value, or failed assertion is
  `violated`;
- incomplete workflow evidence cannot prove that cardinality and stage output
  coverage are satisfied, so otherwise successful checks are `unverified`;
- trace assertions also require complete plan-declared trace telemetry;
- an optional stage may be absent only when workflow completeness is proven.

Each result is bound to the assessed contract digest, plan digest, run ID, and
a canonical digest of the complete `RunSpecEvidence` input. The bundle retains
that digest and the immutable evidence references without blindly copying
potentially sensitive stage outputs.

`assess_controls(...)` remains the control assessor. It intentionally neither
evaluates nor reports run-spec results. Assurance bundles store run-spec results
in the distinct `run-spec-results.json` artifact. The host supplies one
`RunSpecSelection` per run, selecting a declared run spec or explicitly stating
that no run spec applied. Only selected declarations require results; missing
selection or assessment evidence leaves the bundle incomplete.

This keeps the workflow implementation in Python or TypeScript while making its
important stage and evidence invariants reviewable as code.
