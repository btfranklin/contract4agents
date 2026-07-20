# Incident Command

This is the recommended first Contract4Agents example. It defines a typed
incident-response team whose portable contracts can be reviewed independently
of the OpenAI implementation.

## Team

- `IncidentCommander` coordinates investigation and produces `IncidentBrief`.
- `LogInvestigator` searches logs.
- `DeployAnalyst` correlates recent deployments.
- `MetricsAnalyst` measures impact.
- `CustomerImpactWriter` rewrites the final customer-facing summary.

The commander's four named `delegate` edges are materialized as normal native
agent-as-tool relationships. No host-maintained agent registry is needed.

## What to Notice

`capabilities/incident.contract` defines every shared capability once. The
`logs.search` tool is granted to the investigator as `preapproved` and to the
commander as `approval_required`. That difference belongs on the agent-tool
relationship; the OpenAI binding appears only once.

The commander also receives values through two explicit origins:

- `incident.service` is a typed datasource.
- `active_incident` is named external context.

Their Python providers appear only in `contract4agents.targets.toml`.

`assurance.contract` separates an evidence-before-summary control, an
evaluator-facing quality rubric, and an operational latency budget. Approval
controls and typed-output controls are derived automatically from grants and
signatures.

## Run the Offline Loop

From the repository root:

```bash
pdm run python examples/incident-command/data/seed.py
export CONTRACT4AGENTS_INCIDENT_DB="$PWD/examples/incident-command/data/fixture.sqlite"
pdm run contract4agents check examples/incident-command
pdm run contract4agents compile examples/incident-command \
  --out .contract/build/incident-command
pdm run contract4agents plan examples/incident-command \
  --target openai --profile test \
  --out .contract/build/incident-command/plan.json
pdm run contract4agents eval examples/incident-command \
  --target openai --profile test
```

The `test` profile and deterministic eval data use no provider credentials.
Planning validates binding coverage and callable shape without invoking the
fake tools.

## Inspect the Result

Start with:

- `.contract/build/incident-command/ir/contract.json`
- `.contract/build/incident-command/ir/contract-digest.txt`
- `.contract/build/incident-command/instructions/IncidentCommander.md`
- `.contract/build/incident-command/schemas/IncidentBrief.json`
- `.contract/build/incident-command/plan.json`

The instructions contain model-visible goal, guidance, and composition
descriptions. The evaluator-only rubric and hidden control expression do not
appear there.

The plan shows the exact model selection, shared capability bindings,
authorization mechanisms, composition graph, context providers, derived and
explicit controls, host obligations, and expected event types.

## Materialize

With the OpenAI extra installed:

```python
from contract4agents import materialize

system = materialize(
    "examples/incident-command",
    target="openai",
    profile="production",
)

commander = system.agents["IncidentCommander"]
```

`commander` and the specialist values are ordinary OpenAI Agents SDK objects.
Changing the production model is a target-profile edit, not an agent-code edit.

The example's local implementations are deterministic teaching fixtures. A real
application binds the same capability names to production services and owns
credentials, approval decisions, persistence, and incident workflow.
