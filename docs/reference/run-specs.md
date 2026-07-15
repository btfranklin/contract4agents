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
agent signature.

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
assessment.

## Trace Relations

Run specs use normalized trace V2 and therefore inherit contract/plan identity,
semantic IDs, parent relationships, and completeness rules. A missing event can
prove a negative or ordering claim only when the reviewed plan establishes the
necessary telemetry coverage. Otherwise the result is unverified.

## IR and Assurance

Run specs are stored in canonical IR with stable IDs and included in the
contract digest. After a host workflow finishes, its normalized trace, typed
stage outputs, and derived values can be assessed and included in an assurance
bundle.

This keeps the workflow implementation in Python or TypeScript while making its
important stage and evidence invariants reviewable as code.
