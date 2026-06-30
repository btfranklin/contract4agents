# Incident Command Example

This example is a small incident-response team written as Contract4Agents source
files. It is meant to be readable before you know the language.

The scenario: checkout requests are timing out. The team must investigate logs,
recent deploys, and metrics, then produce an evidence-backed incident brief. The
contract says what each agent is allowed to do, what shape its inputs and outputs
must have, and what behavior should be checked.

## What You Would Write

If you were using Contract4Agents for a similar team, you would write files like
the ones in this folder:

- `types/incident.contract`: the data shapes used by the team.
- `agents/incident_commander.contract`: the coordinating agent.
- `agents/log_investigator.contract`: the log specialist.
- `agents/deploy_analyst.contract`: the deploy specialist.
- `agents/metrics_analyst.contract`: the metrics specialist.
- `agents/customer_impact_writer.contract`: the final clarity pass.
- `evals/incident_command.eval`: the scenario expectations.
- `monitors/incident.monitors.contract`: the trace rule for approval-sensitive
  behavior.
- `data/seed.py`: local fake data setup for the example.

The Python files in `../incident_command_imports/` are fake local tools and a
deterministic harness. They stand in for production systems such as logs,
metrics, deploy history, and status-page APIs.

## Read This First

Start with `agents/incident_commander.contract`.

That file shows the main idea:

- the agent accepts a typed incident request, service, and time window;
- it returns an `IncidentBrief`;
- it can call specialist agents;
- it has one approval-gated status-page tool;
- it records policies, success criteria, guards, and assertions.

Then read `types/incident.contract`. Types are the shared vocabulary. For
example, `IncidentBrief` says the final answer must contain a summary, likely
cause, evidence list, and next actions.

## How The Files Fit Together

`types/incident.contract` defines the shapes of inputs and outputs. Agent files
refer to those names instead of repeating object structures.

`agents/incident_commander.contract` is the top-level contract. It composes four
specialists and declares the only tool that needs human approval:
`status_page.draft_update`.

`agents/log_investigator.contract`, `agents/deploy_analyst.contract`, and
`agents/metrics_analyst.contract` each declare one preapproved tool. Their job is
to gather one kind of evidence.

`agents/customer_impact_writer.contract` takes the final brief and improves the
customer-facing wording without removing evidence.

`evals/incident_command.eval` describes a successful scenario. It expects the
brief to match the `IncidentBrief` type, discover the hidden likely cause, call
the investigation tools, call the specialist agents, and avoid the approval-gated
status-page tool.

`monitors/incident.monitors.contract` describes behavior to watch in traces. If
the status-page draft tool is called, the trace must show approval.

`data/seed.py` creates `data/fixture.sqlite`, the local SQLite database used by
the fake tools.

## Run It

From the repository root:

```bash
pdm run python examples/incident-command/data/seed.py
pdm run contract4agents check examples/incident-command
pdm run contract4agents compile examples/incident-command --out .contract/build
pdm run contract4agents visualize examples/incident-command --out .contract/build/visualization
```

`check` reads the source files and validates that references, types, tools,
evals, and monitors are coherent.

`compile` writes generated artifacts under `.contract/build`.

`visualize` writes a human-review graph under `.contract/build/visualization`.
Open `.contract/build/visualization/index.html` in a browser to inspect the
agent, type, tool, eval, and monitor relationships.

The `.contract/` directory is generated local output. It is safe to delete and
regenerate.

## Generated Artifacts

After `compile`, these are the most useful files to inspect:

- `.contract/build/schemas/IncidentBrief.json`: JSON Schema for the final output.
- `.contract/build/manifests/IncidentCommander.json`: the machine-readable
  contract for the coordinating agent.
- `.contract/build/instructions/IncidentCommander.md`: instruction text derived
  from the agent contract.
- `.contract/build/evals/evals.json`: the eval scenario in compiled form.
- `.contract/build/monitors/monitors.json`: the monitor rule in compiled form.
- `.contract/build/adapters/capability-matrix.json`: adapter support notes.
- `.contract/build/docs/summary.md`: a compact generated summary.
- `.contract/build/visualization/index.html`: the static graph.

These files are not hand-edited. The `.contract` source files are the source of
truth.

## What The Artifacts Mean

Schemas answer: what shape does this data have?

Manifests answer: what does this agent require, what can it call, and what rules
or expectations travel with it?

Instructions answer: what prompt-like guidance was derived for this agent?

Eval packs answer: how should a scenario be judged?

Monitor packs answer: what trace behavior should be watched after a run?

The visualization answers: what agents, tools, types, evals, and monitors exist,
and how are they connected?

The adapter capability matrix answers: which parts of the contract can a target
adapter support directly, partially, or with caveats.

## What To Notice

The coordinator does not directly own every skill. It delegates to narrow
specialists for logs, deploys, metrics, and customer-facing wording.

The approval-gated status-page tool exists in the contract, but the eval expects
it not to be called. The monitor shows the opposite case: if that tool is called,
the trace must also show approval.

The fake Python tools and SQLite database are only there so the example can run
offline and produce deterministic traces.
