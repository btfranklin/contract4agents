# Contract4Agents

![Contract4Agents banner](https://raw.githubusercontent.com/btfranklin/contract4agents/main/.github/social%20preview/contract4agents_social_preview.jpg "Contract4Agents")

Contract4Agents is a typed declarative language and local toolchain for defining AI agents as inspectable contracts.

The source artifact is a `.contract` file. It describes an agent's callable interface, allowed capabilities, policies, guards, assertions, and output contract. The compiler turns that source into prompts, provider-neutral manifests, JSON Schemas, eval packs, monitor rules, and visualization artifacts.

Contract4Agents includes the compiler, CLI, local fixtures, monitor checks, runtime primitives, and provider adapters needed to use those contracts beside a host agent application.

Start here:

- `docs/tutorials/using-contract4agents-with-an-agent-app.md` explains how to
  use Contract4Agents beside an existing agent SDK implementation.
- `VISION.md` explains the concept and why it exists.
- `examples/incident-command/README.md` is the most concrete first read: it
  explains what users write, what the files mean, and what artifacts are
  generated.
- `examples/multi-lens-research/README.md` shows a complex research team split
  into focused expert lenses.
- `examples/market-research-brief/README.md` shows document-driven market
  research against dated current-fact snapshots.
- `examples/README.md` explains the reusable pattern for future examples.
- `docs/index.md` is the documentation map.
- `docs/examples/incident-command-walkthrough.md` walks through the clone-only example.
- `docs/research/agent-sdk-pattern-survey.md` captures the cross-SDK patterns Contract4Agents should preserve.
- `docs/decisions/accepted-decisions.md` records choices that should not be reopened casually.
- `docs/implementation/roadmap.md` tracks the active backlog for VISION gaps that are not implemented yet.

## What's Included

- `contract4agents` Python package
- `contract4agents` Click CLI
- Lark-backed parser
- semantic analyzer
- JSON Schema and provider-neutral manifest compiler
- local fake-tool and datasource runtime primitives
- eval and monitor runners
- static project visualization
- first OpenAI adapter and semantic judge adapter
- clone-only `Incident Command` example backed by SQLite fake data

## Development Setup

```bash
pdm install
pdm run contract4agents --help
```

## Useful Commands

```bash
pdm run contract4agents check examples/incident-command
pdm run contract4agents compile examples/incident-command --out .contract/build
pdm run contract4agents visualize examples/incident-command --out .contract/build/visualization
pdm run contract4agents eval tests/fixtures/contract_projects/ops-desk-lab
pdm run docs-check
pdm run validate
```

The `examples/incident-command` project is the public walkthrough fixture for check, compile, and visualization. The eval command currently uses the richer `tests/fixtures/contract_projects/ops-desk-lab` fixture because the reusable fixture runner expects a `fixture.json` project.

## OpenAI Adapter Checks

The normal validation suite does not call external APIs. Live OpenAI checks are opt-in:

```bash
CONTRACT4AGENTS_RUN_OPENAI_LIVE=1 pdm run test:openai-live
CONTRACT4AGENTS_RUN_OPENAI_AGENT_LIVE=1 pdm run test:openai-agent-live
```

Those commands require `OPENAI_API_KEY` in the environment or in the ignored local `.env` file.

## License

MIT. See `LICENSE`.
