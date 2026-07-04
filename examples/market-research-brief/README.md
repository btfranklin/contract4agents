# Market Research Brief Example

This example models a document-driven market research team that compares
internal documents with a seeded snapshot of current external facts.

The scenario: a team is evaluating whether auditable AI summaries are a strong
opportunity for field operations buyers. Internal documents contain customer and
sales signals, but current-world claims must be checked against dated external
facts before the final report states them.

## What This Example Demonstrates

- Separation between internal documents and current external facts.
- Focused agents for document analysis, current-truth checking, competitors,
  customer signals, and final report writing.
- Policies that prevent stale internal claims from being treated as current
  market truth.
- Guards and assertions around structured report output.
- Evals that verify both output and trace behavior.
- A monitor that catches reports built from internal documents without fetching
  current facts.

## What You Would Write

The example source files are:

- `types/market.contract`: shared data shapes for the question, evidence,
  competitor snapshot, customer signals, and final report.
- `agents/market_research_lead.contract`: the coordinating agent.
- `agents/document_analyst.contract`: extracts internal document evidence.
- `agents/current_truth_scout.contract`: checks dated external-fact snapshots.
- `agents/competitor_analyst.contract`: compares competitor signals.
- `agents/customer_signal_analyst.contract`: summarizes customer pain points.
- `agents/report_writer.contract`: writes the final report.
- `evals/market_research.eval`: expected behavior for the field-ops scenario.
- `monitors/market.monitors.contract`: monitor rule requiring current-fact
  evidence when internal documents are used.
- `data/seed.py`: local fake data setup.

The Python files in `../market_research_brief_imports/` are deterministic fake
tools and a harness used by tests.

## Read This First

Start with `agents/market_research_lead.contract`.

That file shows the core split: internal-document review, current-fact checking,
competitor analysis, customer-signal analysis, and report writing are separate
agent responsibilities.

Then read `agents/report_writer.contract`. It shows the most important policy:
current market claims require current-fact citations, while internal documents
remain internal evidence.

## How The Files Fit Together

`DocumentAnalyst` finds internal product, sales, or customer documents and
extracts a cited internal claim.

`CurrentTruthScout` fetches dated external facts so the report does not treat
old internal documents as current truth.

`CompetitorAnalyst` looks up competitor positioning for the target segment.

`CustomerSignalAnalyst` summarizes customer pain points from internal evidence.

`ReportWriter` combines those specialist outputs into a
`MarketOpportunityReport` with citations and freshness notes.

`MarketResearchLead` coordinates the team and owns the final report. Its
`host_context` declaration names the intermediate evidence, competitor, and
customer-signal values that the host passes between child agents.
`CurrentTruthScout` also declares `use hosted_tool openai.web_search
context_size "medium"` to show how provider-native hosted tools appear
separately from host Python tools in manifests and visualization artifacts.
`contract4agents.registry.json` maps local fake tools, the hosted web-search
configuration, and host-provided intermediate context to the surfaces checked by
`--strict-drift`.

## Run It

From the repository root:

```bash
pdm run python examples/market-research-brief/data/seed.py
pdm run contract4agents check examples/market-research-brief
pdm run contract4agents check examples/market-research-brief --strict-drift
pdm run contract4agents compile examples/market-research-brief --out .contract/build/market-research-brief
pdm run contract4agents visualize examples/market-research-brief --out .contract/build/market-research-brief/visualization
pdm run contract4agents eval examples/market-research-brief
```

`check` validates the source. `compile` writes generated review artifacts.
`visualize` writes the static graph. `eval` runs the deterministic local fixture
and reports skipped semantic checks separately from deterministic pass/fail
results.

The `.contract/` directory is generated local output. It is safe to delete and
regenerate.

## Generated Artifacts

After `compile`, inspect:

- `schemas/MarketOpportunityReport.json`: JSON Schema for the final report.
- `manifests/MarketResearchLead.json`: machine-readable contract for the
  coordinating agent.
- `instructions/MarketResearchLead.md`: generated instructions for the lead.
- `evals/evals.json`: compiled eval expectations.
- `monitors/monitors.json`: compiled monitor rules.
- `guards/guard-plan.json`: guard enforcement metadata.
- `adapters/capability-matrix.json`: adapter support notes.
- `docs/summary.md` and `docs/agents/*.md`: generated review docs.
- `visualization/index.html`: static review graph.

The `.contract` and `.eval` files are the source of truth. Generated artifacts
are review output.
