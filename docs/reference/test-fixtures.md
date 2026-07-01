# Test Fixture Reference

Contract4Agents' multi-agent fixture runner lives in `contract4agents.fixtures` and is used by both tests and `contract4agents eval` when a project has `fixture.json`.

The public smoke project lives at `examples/incident-command/`. The richer internal fixture lives at `tests/fixtures/contract_projects/ops-desk-lab/` so tests can exercise edge cases, guardrails, and failure paths without making the beginner example noisy.

Run the local deterministic fixture:

```bash
pdm run test:agent-fixture
pdm run contract4agents eval examples/incident-command
pdm run contract4agents eval tests/fixtures/contract_projects/ops-desk-lab
```

Run the live OpenAI Agents SDK fixture:

```bash
CONTRACT4AGENTS_RUN_OPENAI_AGENT_LIVE=1 pdm run test:openai-agent-live
```

The live run loads `OPENAI_API_KEY` from the environment, falling back to the ignored local `.env` file. It never prints the key. Override the model with `CONTRACT4AGENTS_OPENAI_AGENT_MODEL`; the default is `gpt-5.5`.

## Fixture Contracts

Each fixture project must include `fixture.json` with:

- `entry_agent` and `output_type`
- Python references for `seed`, `hidden_truth`, `starts`, and `local_runner`
- expected agents, types, tools, tool permissions, datasources, eval count, and monitor count

Use `live_runner` only for fixture projects that support the opt-in OpenAI mode.

`expected.tool_permissions` is a map of tool name to required compiled
permission. Use it for fixture-specific approval or denial expectations, for
example:

```json
{
  "expected": {
    "tools": ["status_page.draft_update", "incident.lookup"],
    "tool_permissions": {
      "status_page.draft_update": "requires_approval"
    }
  }
}
```

The runner compiles the project into a temp build directory, runs compile check mode against that output, verifies key artifact contents, then runs starts. Execution does not begin if artifact verification fails.
For each start, the runner evaluates the matching `.eval` case, then evaluates
compiled assertions for the fixture `entry_agent`, then runs project monitors.
Reports keep eval failures, assertion failures, monitor violations, and skipped
semantic checks in separate fields.

## Reports And Cleanup

Each run writes generated artifacts, trace JSONL files, `report.json`, and `report.md` inside the run directory while execution is active. By default, cleanup removes transient generated material such as `build/`, `data/`, and `traces/` only after successful runs, but keeps `reports/report.json` and `reports/report.md` as the surviving record of what happened. Failed runs keep artifacts and traces for debugging.

Set `CONTRACT4AGENTS_KEEP_FIXTURE_ARTIFACTS=1` to keep the transient artifacts alongside the reports for debugging.
