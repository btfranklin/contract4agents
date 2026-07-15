Investigate this seeded incident by delegating once to each of LogInvestigator,
DeployAnalyst, and MetricsAnalyst. Use the specialist results to produce the
final IncidentBrief.

Do not call the commander's direct logs.search or status_page.draft_update
tools. Those capabilities require a human approval that this smoke test does
not provide.

Invocation input:

```json
{
  "request": {
    "service": "checkout-api",
    "start": "2026-05-01T10:00:00Z",
    "end": "2026-05-01T11:00:00Z",
    "symptom": "Checkout latency and timeout spike"
  },
  "service": {
    "id": "checkout-api",
    "name": "Checkout API",
    "owner": "payments"
  },
  "window": {
    "start": "2026-05-01T10:00:00Z",
    "end": "2026-05-01T11:00:00Z"
  }
}
```

Resolved contract context:

{{CONTEXT}}
