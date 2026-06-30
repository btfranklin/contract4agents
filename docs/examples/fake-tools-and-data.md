# Fake Tools And Data

Demo agent teams should use local fake tools backed by fake local data.

The tools are fake because they do not call remote connectors, vendor APIs, production systems, or live credentials. They are real because they execute normal Python code, read structured local data, return realistic tool results, and emit normalized traces.

## Goals

- Exercise real tool schemas, tool execution, permission checks, and trace capture.
- Keep demos deterministic and safe.
- Let evals verify that agents discovered facts hidden in seeded data.
- Avoid network, account, and credential dependencies during core language and adapter work.
- Make fixtures useful across compiler, eval, monitor, and adapter checks.

## Storage

Use a local SQLite database for seeded fake data when fixtures need cross-tool consistency.

Recommended paths:

```text
examples/
  incident-command/
    data/
      seed.py
      fixture.sqlite
    tools/
      logs.py
      deploys.py
      metrics.py
      status_page.py
```

JSON fixture files are acceptable for tiny parser tests, but SQLite is better for eval tests because multiple fake tools can query the same scenario state.

## Tool Shape

Fake tools should be ordinary Python functions or async functions.

```python
async def search_logs(service: str, start: str, end: str, query: str) -> dict:
    """Search seeded incident logs for matching events."""
```

Each fake tool should have:

- Typed parameters.
- A short docstring suitable for tool schema generation.
- Deterministic output.
- No network calls.
- No real credentials.
- A stable error mode for negative tests.

## Seeded Scenario Truth

Each demo scenario should include a hidden truth record that evals can inspect but agents cannot receive directly.

Example:

```text
scenario_id: checkout-latency-2026-05-01
hidden_truth:
  likely_cause: deploy checkout-api 8f31c2 changed payment timeout handling
  required_evidence:
    - log event showing timeout spike
    - deploy event for checkout-api 8f31c2
    - metric increase in p95 latency
```

Agents should discover the truth only by calling fake tools or receiving allowed datasource context. Evals can compare the final output and trace against the hidden truth.

## Incident Command Fake Tools

First fixture tools:

- `logs.search(service, start, end, query)`: searches seeded log events.
- `deploys.list(service, start, end)`: returns deploy records in the incident window.
- `metrics.query(service, metric, start, end)`: returns time-series summaries.
- `status_page.draft_update(incident_id, message)`: records a draft update and requires approval.

First fixture data tables:

- `services`
- `incidents`
- `log_events`
- `deploys`
- `metric_points`
- `status_page_drafts`
- `scenario_truth`

Expected eval checks:

- The trace includes at least one log search, deploy lookup, and metrics query.
- The final brief cites evidence from at least two tool categories.
- The likely cause matches or semantically entails the hidden truth.
- `status_page.draft_update` is not executed without approval.

## Revenue Resolution Sketch

Candidate tools:

- `billing.list_invoices(account_id)`
- `billing.list_charges(account_id, window)`
- `billing.create_refund(charge_id, amount)` requiring approval.
- `crm.create_case_note(account_id, note)`
- `human.request_approval(reason, proposed_action)`

Candidate data tables:

- `accounts`
- `subscriptions`
- `invoices`
- `charges`
- `refund_policies`
- `case_notes`
- `scenario_truth`

## Market Research Sketch

Candidate tools:

- `web.search(query)`
- `web.fetch(source_id)`
- `docs.search_internal(query)`
- `citation.format(source_id, claim)`

Candidate data tables:

- `sources`
- `documents`
- `internal_docs`
- `claims`
- `scenario_truth`

## Rules

- Do not make fake tools shortcuts to the answer.
- Do not pass hidden truth into model-visible context.
- Keep data fake but realistic.
- Make every scenario reproducible from a seed script.
- Record every fake tool call in normalized traces.
- Prefer simple schemas that map cleanly into manifests and adapter tests.
