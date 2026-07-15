# Deterministic Eval Data

Public examples use deterministic data to exercise contracts, plans, traces,
controls, and quality criteria without provider credentials. The data is an
eval-provider input, not a second description of the agent system.

## Responsibilities

Contracts and the target plan already define:

- agents, capabilities, grants, and authorization;
- composition, context, and isolation;
- controls, rubrics, and expected telemetry;
- input and output schemas.

`eval-data.json` supplies only case-specific facts:

- fixture values and optional trial input overrides;
- normalized output and trace events;
- approval decisions;
- semantic judge decisions;
- latency, cost, and token metrics.

It must not contain expected agent counts, permission inventories, prompts,
agent factories, tool registries, or output-type mappings.

## File Shape

```json
{
  "schema_version": "1",
  "cases": {
    "eval:IncidentCommander:discovers_checkout_cause": {
      "inputs": {
        "hidden_truth": {
          "likely_cause": {
            "contains_all": ["deploy", "checkout-api"]
          }
        }
      },
      "trials": [
        {
          "output": {
            "summary": "Checkout latency followed the latest checkout-api deploy.",
            "likely_cause": "checkout-api deploy",
            "evidence": ["log-17", "deploy-42", "metric-9"],
            "next_actions": ["roll back deploy-42", "watch checkout latency"]
          },
          "events": [
            {
              "event_type": "tool.completed",
              "semantic": {
                "agent_id": "agent:LogInvestigator",
                "capability_id": "tool:logs.search",
                "grant_id": "grant:LogInvestigator:logs.search"
              }
            }
          ],
          "approvals": {
            "tool:status_page.draft_update": false
          },
          "judges": {
            "quality:IncidentCommander:concise_operational_summary": {
              "status": "passed",
              "reason": "The brief is concise and cites all material claims.",
              "score": 0.95,
              "provider": "file",
              "version": "1"
            }
          },
          "metrics": {
            "latency_ms": 1800,
            "cost_usd": 0.01,
            "input_tokens": 900,
            "output_tokens": 220
          }
        }
      ]
    }
  }
}
```

The loader fills run IDs, contract/plan digests, default provider correlation,
event IDs, parent relationships, evidence references, provenance, and safe
redaction metadata when omitted. Explicit values are still strictly validated.

## Seed Scripts

Example `data/seed.py` scripts should be deterministic and idempotent. Use
stable IDs and timestamps, delete or replace prior generated state, and avoid
network access. Generated data should live in ignored paths or in the committed
small `eval-data.json` when the file itself is the teaching artifact.

## Fake Implementations

Target-bound local Python tools and datasource providers should:

- match the portable input/output shape;
- return stable results for stable inputs;
- record side effects explicitly rather than performing external writes;
- fail loudly on unknown IDs or malformed data;
- avoid hidden authorization logic that contradicts the contract grant;
- remain ordinary application functions, not test-only agent configuration.

Approval decisions belong to the eval provider or host, not inside the tool.

## Negative Cases

Useful deterministic campaigns include:

- missing required tool or composition evidence;
- a denied approval;
- incomplete telemetry yielding `unverified`;
- a violated explicit control;
- a judge error yielding unverified quality;
- latency/cost threshold failure;
- a baseline regression.

The negative data should test retained assurance behavior. Do not add cases whose
only purpose is to prove removed syntax no longer exists.
