# Demo Agent Teams

The customer-support and revenue-resolution examples started as brainstorming sketches. The current public demo surface is stronger and exercises the patterns found in the SDK survey.

These three teams are current because together they cover read-only evidence gathering, approval-gated side effects, hosted-tool declarations, multi-agent composition, structured outputs, hidden context, semantic evals, trace spies, and monitor rules.

## Team 1: Incident Command

Purpose: investigate a production incident and produce an evidence-backed incident brief.

Agents:

- `IncidentCommander`: owns the final incident brief and delegates investigation.
- `LogInvestigator`: searches service logs and extracts relevant errors.
- `DeployAnalyst`: inspects recent deploys and configuration changes.
- `MetricsAnalyst`: inspects service health, latency, error rate, and saturation metrics.
- `CustomerImpactWriter`: converts technical findings into customer-facing impact language.

Typed context slots:

- `IncidentReportRequest`
- `ServiceCatalogEntry`
- `TimeWindow`
- `RecentDeploys`
- `LogEvidenceBundle`
- `MetricSnapshot`
- `IncidentBrief`

Datasources:

- `ServiceCatalogEntry` from service name.
- `RecentDeploys` from service and time window.
- `MetricSnapshot` from service and time window.

Tools:

- `logs.search`
- `deploys.list`
- `metrics.query`
- `status_page.draft_update` requiring approval.

Contract4Agents features exercised:

- Read-only tools.
- Parallel specialist agents.
- Evidence citation requirements.
- Monitor rule for unsupported cause claims.
- Semantic eval for whether the final brief is clear and operationally useful.
- Trace assertions that `status_page.draft_update` is not called without approval.

Why it is a good fixture:

- It mirrors a real operations process.
- It has a clear distinction between evidence, inference, and external communication.
- It exercises both deterministic trace checks and qualitative judgment.

## Team 2: Multi-Lens Research

Purpose: produce a sourced research brief by splitting evidence, technical, policy, counterargument, and synthesis work into separate lenses.

Agents:

- `ResearchDirector`: owns the final research workflow and review gate.
- `EvidenceMapper`: searches, fetches, scores, and cites seeded sources.
- `TechnicalLensAnalyst`: evaluates technical evidence.
- `PolicySafetyLensAnalyst`: evaluates policy and safety implications.
- `CounterargumentAnalyst`: finds contrary evidence and weak points.
- `SynthesisWriter`: writes the final structured brief.

Typed context slots:

- `ResearchQuestion`
- `SourceEvidence`
- `EvidenceMap`
- `TechnicalAssessment`
- `PolicySafetyAssessment`
- `CounterargumentSet`
- `ResearchBrief`

Tools:

- `sources.search`
- `sources.fetch`
- `evidence.score`
- `citation.format`
- `expert_review.request` requiring approval.

Contract4Agents features exercised:

- Multi-agent composition.
- Approval-gated review requests.
- Structured intermediate outputs.
- Semantic evals for balanced, source-backed synthesis.
- Monitor rule for final-output evidence quality.

Why it is a good fixture:

- It makes the model gather evidence before synthesis.
- It tests whether specialist outputs stay distinct before final writing.
- It exercises both deterministic trace checks and skipped semantic checks.

## Team 3: Market Research Brief

Purpose: produce a market-entry brief from local documents, dated current-fact snapshots, customer signals, competitor data, and a hosted web-search declaration.

Agents:

- `MarketResearchLead`: owns the final report and delegates specialist work.
- `DocumentAnalyst`: reads seeded internal documents.
- `CurrentTruthScout`: checks dated current-fact snapshots and declares hosted web search.
- `CompetitorAnalyst`: compares seeded competitor records.
- `CustomerSignalAnalyst`: extracts customer-signal evidence.
- `ReportWriter`: writes the final structured report.

Typed context slots:

- `MarketResearchQuestion`
- `DocumentEvidence`
- `CurrentFactEvidence`
- `CompetitorSnapshot`
- `CustomerSignalSummary`
- `MarketOpportunityReport`

Tools:

- `documents.search`
- `documents.fetch`
- `current_facts.search`
- `current_facts.fetch`
- `competitors.lookup`
- `citation.format`
- Hosted tool declaration: `openai.web_search`


Contract4Agents features exercised:

- Hosted-tool metadata in artifacts and visualization.
- Separation between internal documents and current facts.
- Structured output with citations and freshness notes.
- Semantic evals for source freshness and claim support.
- Monitor rule for final-output evidence requirements.

Why it is a good fixture:

- It proves hosted tools can be declared without making the offline fixture call the network.
- It makes freshness and source category visible in outputs.
- It validates richer public examples with the same `eval` command as Incident Command.

## Recommended Fixture Order

1. Start with `Incident Command` for parser, semantic analyzer, and trace-spy fixtures.
2. Add `Multi-Lens Research` for specialist composition, approval-gated review, and source-evidence checks.
3. Add `Market Research Brief` for hosted-tool metadata, freshness checks, and richer evidence categories.

This order gives implementation a useful progression from read-only investigation to specialist synthesis to hosted-tool-aware research quality.

## Historical/Future Sketch: Revenue Resolution

`Revenue Resolution` is not part of the current public example surface. Keep it as future sketch material only if the repo later needs a billing-flavored approval and permission example.

## Local Fake Tools And Data

Every demo team should include local fake tools backed by fake local data. These tools should be real Python modules that execute through runtime primitives and emit normal traces, but they should not call remote connectors, vendor APIs, or live credentials.

Use `docs/examples/fake-tools-and-data.md` as the fixture contract.

Recommended approach:

- Seed a local SQLite database for each demo team.
- Include hidden scenario truth that evals can read but agents cannot see directly.
- Make agents discover the truth through normal tool calls and datasources.
- Keep all data fake, deterministic, and reproducible from seed scripts.
- Use tool outputs realistic enough to expose weak prompts, bad routing, missing evidence, or unsafe side effects.

## Fixture Design Rules

- Keep fixtures realistic enough to expose weak abstractions.
- Keep data fake and local.
- Back fixtures with local Python fake tools.
- Use hidden seeded truth to validate scenario discovery.
- Make traces part of expected behavior.
- Include at least one semantic eval in every team.
- Include adapter caveat tests where they clarify real OpenAI behavior.
- Avoid turning demo data into a toy chatbot benchmark.
