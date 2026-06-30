# Test Fixture Reference

Contract4Agents' multi-agent fixture runner lives in `contract4agents.fixtures` and is used by both tests and `contract4agents eval` when a project has `fixture.json`.

The default project lives at `tests/fixtures/contract_projects/ops-desk-lab/`. It is intentionally separate from `examples/` so the fixture can exercise edge cases, guardrails, and failure paths without becoming user-facing demo code.

Run the local deterministic fixture:

```bash
pdm run test:agent-fixture
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
- Python references for `seed`, `hidden_truth`, `starts`, `local_runner`, and `live_runner`
- expected agents, types, tools, datasources, eval count, and monitor count

The runner compiles the project into a temp build directory, runs compile check mode against that output, verifies key artifact contents, then runs starts. Execution does not begin if artifact verification fails.

## Reports And Cleanup

Each run writes generated artifacts, trace JSONL files, `report.json`, and `report.md` inside the run directory while execution is active. By default, cleanup removes transient generated material such as `build/`, `data/`, and `traces/` only after successful runs, but keeps `reports/report.json` and `reports/report.md` as the surviving record of what happened. Failed runs keep artifacts and traces for debugging.

Set `CONTRACT4AGENTS_KEEP_FIXTURE_ARTIFACTS=1` to keep the transient artifacts alongside the reports for debugging.
