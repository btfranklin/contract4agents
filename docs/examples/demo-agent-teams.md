# Demo Agent Teams

The customer-support example in this repo started as a brainstorming sketch. The first real demo data should be stronger and should exercise the patterns found in the SDK survey.

These three teams are recommended because together they cover read-only evidence gathering, approval-gated side effects, multi-agent composition, structured outputs, datasources, hidden context, semantic evals, trace spies, and monitor rules.

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

## Team 2: Revenue Resolution

Purpose: triage billing, subscription, and refund requests without unsafe financial side effects.

Agents:

- `RevenueTriageAgent`: classifies the request and routes to the right specialist.
- `InvoiceResearchAgent`: gathers invoices, payments, and account history.
- `RefundPolicyAgent`: applies refund policy to the evidence.
- `CustomerReplyAgent`: writes the customer-facing reply.
- `ApprovalCoordinator`: prepares approval requests for irreversible actions.

Typed context slots:

- `CustomerMessage`
- `AccountProfile`
- `BillingHistory`
- `ChargeEvidence`
- `RefundPolicy`
- `RefundRecommendation`
- `CustomerReply`

Datasources:

- `AccountProfile` from authenticated account ID.
- `BillingHistory` from account profile.
- `RefundPolicy` from current plan and region.

Tools:

- `billing.list_invoices`
- `billing.list_charges`
- `billing.create_refund` requiring approval.
- `crm.create_case_note`
- `human.request_approval`

Contract4Agents features exercised:

- Approval-gated tools.
- Deny rules and pre-approved read-only tools.
- Structured output contract for refund recommendations.
- Hidden account state versus rendered customer-safe context.
- Trace spies for required evidence before any refund recommendation.
- Semantic eval for non-technical customer copy.

Why it is a good fixture:

- It has realistic safety constraints.
- It distinguishes recommendation from execution.
- It tests the permission model better than a simple support chatbot.

## Team 3: Market Research Brief

Purpose: produce a sourced market-entry brief from mixed web, document, and internal context.

Agents:

- `ResearchLead`: owns the final structured brief.
- `SourceScout`: finds and ranks candidate sources.
- `EvidenceExtractor`: extracts claims and citations from approved sources.
- `SkepticReviewer`: challenges weak claims and missing counterevidence.
- `BriefWriter`: produces the final narrative and structured summary.

Typed context slots:

- `ResearchQuestion`
- `ResearchScope`
- `SourceList`
- `EvidenceTable`
- `CounterEvidence`
- `MarketBrief`

Datasources:

- `ResearchScope` from user request and project defaults.
- `ApprovedSourcePolicy` from project settings.

Tools:

- `web.search`
- `web.fetch`
- `docs.search_internal`
- `citation.format`

Contract4Agents features exercised:

- Agent-as-tool manager pattern.
- Semantic evals for claim support and balanced reasoning.
- Output schema with citations.
- Monitor for uncited factual claims.

Why it is a good fixture:

- It exercises agent teams without risky side effects.
- It makes trace-level behavior central because the final output can look plausible while the source path is weak.
- It pushes Contract4Agents to represent semantic quality checks as first-class evals.

## Recommended Fixture Order

1. Start with `Incident Command` for parser, semantic analyzer, and trace-spy fixtures.
2. Add `Revenue Resolution` for permissions, approvals, and output schemas.
3. Add `Market Research Brief` for semantic evals, citation monitors, and source-evidence checks.

This order gives implementation a useful progression from read-only investigation to guarded action to open-ended research quality.

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
