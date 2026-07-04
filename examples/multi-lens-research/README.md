# Multi-Lens Research Example

This example models a research team that answers a high-stakes decision question
through focused specialist lenses before writing a final brief.

The scenario: an organization is considering a staged rollout for agent
recommendations. The team must map evidence, evaluate technical feasibility,
review policy and safety risk, find counterarguments, and synthesize a balanced
recommendation.

## What This Example Demonstrates

- Narrow specialist agents with limited responsibilities.
- Tool access scoped to the agents that need it.
- Policies that require source-backed claims and visible uncertainty.
- Guards that preserve output shape and approval intent.
- Evals that check both the final brief and the trace.
- A monitor that catches expert-review requests without approval.

## What You Would Write

The example source files are:

- `types/research.contract`: shared data shapes for the research question,
  evidence, specialist assessments, counterarguments, and final brief.
- `agents/research_director.contract`: the coordinating agent.
- `agents/evidence_mapper.contract`: maps and scores source evidence.
- `agents/technical_lens_analyst.contract`: reviews feasibility and
  implementation risk.
- `agents/policy_safety_lens_analyst.contract`: reviews policy and safety risk.
- `agents/counterargument_analyst.contract`: looks for contrary evidence and
  weak assumptions.
- `agents/synthesis_writer.contract`: writes the final brief from specialist
  outputs.
- `evals/multi_lens_research.eval`: expected behavior for the staged-rollout
  scenario.
- `monitors/research.monitors.contract`: approval-sensitive monitor rule.
- `data/seed.py`: local fake data setup.

The Python files in `../multi_lens_research_imports/` are deterministic fake
tools and a harness used by tests.

## Read This First

Start with `agents/research_director.contract`.

That file shows the team shape: one coordinator, five focused specialists, and
one approval-gated tool named `expert_review.request`.

Then read `evals/multi_lens_research.eval`. It shows what the example considers
successful: the final output must conform to `ResearchBrief`, discover the
hidden conclusion, call the specialist agents, use source and citation tools, and
avoid unapproved expert review.

## How The Files Fit Together

`EvidenceMapper` searches the seeded corpus, fetches source records, scores
evidence, and formats citations.

`TechnicalLensAnalyst` reviews implementation feasibility. It does not own
policy or safety judgment.

`PolicySafetyLensAnalyst` reviews policy and safety risk. It does not own
implementation planning.

`CounterargumentAnalyst` looks for disconfirming evidence and weak assumptions.

`SynthesisWriter` writes the final brief from specialist outputs.

`ResearchDirector` coordinates the whole team and owns the final
`ResearchBrief`. Its `host_context` declaration names the intermediate
specialist outputs that host orchestration passes between child agents.
`contract4agents.registry.json` maps the fake source, evidence, citation, and
expert-review tools to importable Python callables and marks those host-provided
intermediate values.

## Run It

From the repository root:

```bash
pdm run python examples/multi-lens-research/data/seed.py
pdm run contract4agents check examples/multi-lens-research
pdm run contract4agents check examples/multi-lens-research --strict-drift
pdm run contract4agents compile examples/multi-lens-research --out .contract/build/multi-lens-research
pdm run contract4agents visualize examples/multi-lens-research --out .contract/build/multi-lens-research/visualization
pdm run contract4agents eval examples/multi-lens-research
```

`check` validates the source. `compile` writes generated review artifacts.
`visualize` writes the static graph. `eval` runs the deterministic local fixture
and reports skipped semantic checks separately from deterministic pass/fail
results.

The `.contract/` directory is generated local output. It is safe to delete and
regenerate.

## Generated Artifacts

After `compile`, inspect:

- `schemas/ResearchBrief.json`: JSON Schema for the final brief.
- `manifests/ResearchDirector.json`: machine-readable contract for the
  coordinating agent.
- `instructions/ResearchDirector.md`: generated instructions for the director.
- `evals/evals.json`: compiled eval expectations.
- `monitors/monitors.json`: compiled monitor rules.
- `guards/guard-plan.json`: guard enforcement metadata.
- `adapters/capability-matrix.json`: adapter support notes.
- `docs/summary.md` and `docs/agents/*.md`: generated review docs.
- `visualization/index.html`: static review graph.

The `.contract` and `.eval` files are the source of truth. Generated artifacts
are review output.
