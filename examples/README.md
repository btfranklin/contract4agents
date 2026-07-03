# Examples

Examples are the best first place to understand Contract4Agents. Each example is
a small contract project that shows what a user writes, what the tool checks, and
what generated artifacts mean.

Examples are repository learning material. They are not included in the installed
Python package.

## Current Example

- `incident-command/`: a production incident investigation team. It shows typed
  inputs and outputs, one coordinating agent, specialist subagents, tool
  permissions, eval expectations, monitor rules, fake local tools, and generated
  review artifacts.
- `multi-lens-research/`: a high-stakes research brief team. It shows focused
  expert lenses, source-backed synthesis, counterargument handling, approval
  guardrails, evals, and monitors.
- `market-research-brief/`: a document-driven market research team. It shows
  internal document review, dated current-fact snapshots, competitor signals,
  customer-signal analysis, an OpenAI hosted web-search declaration, evals, and
  monitors.

Start with [incident-command/README.md](incident-command/README.md).

## What Users Write

A Contract4Agents example should make these pieces visible:

- `types/`: shared data shapes. These are the nouns the agents pass around.
- `agents/`: agent contracts. These define inputs, output type, tools,
  subagents, policies, guards, assertions, and success criteria.
- `evals/`: expectations for a scenario. These describe what a successful run
  should produce and what should appear in the trace.
- `monitors/`: trace rules for behavior that should be watched after a run.
- `data/`: optional local seed data for deterministic fake tools.
- `contract4agents.registry.json`: optional capability registry for strict
  host-code drift checks.
- importable Python helpers: optional local tools or harness code used by tests
  and demos.
- `README.md`: the human explanation of the example.

## What The Tool Generates

The source files are the files under the example directory. Commands write
generated artifacts to `.contract/build`, which is disposable local output.

Common generated files:

- `schemas/*.json`: JSON Schemas for the declared types.
- `manifests/*.json`: machine-readable agent contracts.
- `instructions/*.md`: instruction text derived from each agent contract.
- `evals/evals.json`: compiled eval expectations.
- `monitors/monitors.json`: compiled monitor rules.
- `guards/guard-plan.json`: host and adapter enforcement metadata for guards.
- `adapters/capability-matrix.json`: what each adapter can support directly,
  partially, or with caveats.
- `docs/summary.md`: generated project review summary.
- `docs/agents/*.md`: generated per-agent review pages.
- `visualization/index.html`: static graph for human review.

## Reusable Example Pattern

Future examples should use this shape unless there is a strong reason not to:

```text
examples/example-name/
  README.md
  types/
    domain.contract
  agents/
    coordinator.contract
    specialist.contract
  evals/
    scenario.eval
  monitors/
    behavior.monitors.contract
  contract4agents.registry.json
  data/
    seed.py
```

The README for each example should answer these questions in plain language:

1. What real-world situation does this model?
2. What would I write if I were using Contract4Agents?
3. Which source file should I read first?
4. What does each source file do?
5. Which commands should I run?
6. What generated artifacts will I see, and what do they mean?

Keep examples small enough to read, deterministic enough to run offline, and
specific enough that the generated artifacts have a clear meaning.
