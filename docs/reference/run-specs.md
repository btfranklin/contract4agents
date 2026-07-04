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
    ]
```

Stage suffixes define output cardinality:

- no suffix: exactly one output is required.
- `?`: the stage is optional.
- `+`: one or more outputs are required.

Run spec assertions are trace expressions over the normalized trace. Use
stage outputs for schema checks and trace assertions for ordering, cardinality,
and capability-use expectations.

## Compiler Artifact

The compiler emits `run-specs/run-specs.json`. Each run spec records:

- run spec name and source path
- stage name, agent, output type, cardinality, manifest reference, and schema reference
- trace assertions

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
    run_id="run-123",
)
```

`stage_outputs` is keyed by stage name. Repeated `+` stages expect a non-empty
sequence of outputs; required and optional single stages expect one output
object when present. Each output is validated against the declared stage output
schema.

Single-run traces can omit `run_id`. Multi-run traces must pass `run_id` so
events from separate runs cannot satisfy the same run spec.
