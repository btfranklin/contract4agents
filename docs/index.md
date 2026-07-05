# Documentation Index

This directory is the system of record for Contract4Agents design and implementation guidance.

## Reading Paths

For product intent:

- Read `../VISION.md`.
- Then read `architecture/system-design.md`.
- For the smallest practical adoption path, read
  `tutorials/first-contract-project.md`.
- For the deeper SDK integration guide, read
  `tutorials/using-contract4agents-with-an-agent-app.md`.
- To understand the project from a concrete example, read
  `../examples/incident-command/README.md`.

For language implementation:

- Read `language/contract-language.md`.
- Then read `compiler/compiler-outputs.md`.
- Use `architecture/parser-internals.md` when changing parser internals.
- Use `research/agent-sdk-pattern-survey.md` to understand adapter targets.
- Use `examples/demo-agent-teams.md` to choose the first parser and runtime fixtures.
- Use examples embedded in those docs as parser fixture candidates.

For runtime implementation:

- Read `runtime/context-and-datasources.md`.
- Then read `evaluation/evals-assertions-monitors.md`.
- Use `reference/run-specs.md` for host-owned workflow expectation checks.

For validation and release readiness:

- Read `quality/validation.md`.
- Read `releasing.md` before tagging or publishing a package release.
- Use `reference/cli.md` for command behavior, `reference/test-fixtures.md` for fixture-runner behavior, and
  `reference/vscode-extension.md` for editor-extension packaging.

For project planning:

- Read `decisions/open-questions.md`.
- Resolve open questions in the relevant topical docs when implementation settles them.

## Documents

- `architecture/system-design.md`: system components, boundaries, and execution flow.
- `architecture/parser-internals.md`: parser module boundaries and the Lark-to-AST flow.
- `language/contract-language.md`: source file structure, declarations, syntax, and semantic rules.
- `runtime/context-and-datasources.md`: typed context slots, Python datasource interface, resolution algorithm, and provenance.
- `compiler/compiler-outputs.md`: compiler phases, generated artifacts, diagnostics, and static checks.
- `evaluation/evals-assertions-monitors.md`: `.eval` files, trace spies, assertions, guards, and monitor rules.
- `quality/validation.md`: local validation commands, packaging checks, release pipeline, generated artifacts, and live-test boundaries.
- `tutorials/first-contract-project.md`: shortest path from one existing agent to one compiled contract project.
- `tutorials/using-contract4agents-with-an-agent-app.md`: deeper guide for using Contract4Agents beside an existing agent SDK implementation.
- `reference/grammar.md`: implemented V1 grammar surface.
- `reference/manifest.md`: provider-neutral manifest reference.
- `reference/capability-registry.md`: source-owned capability registry and strict drift checks.
- `reference/visualization.md`: static project visualization command and artifact reference.
- `reference/trace-schema.md`: normalized trace event reference.
- `reference/run-specs.md`: run spec source syntax, artifacts, and runtime evaluation API.
- `reference/eval-language.md`: deterministic, trace-spy, hidden-truth, and semantic eval syntax.
- `reference/cli.md`: CLI command reference.
- `reference/openai-adapter.md`: OpenAI execution adapter notes.
- `reference/semantic-judge.md`: OpenAI semantic judge notes.
- `reference/test-fixtures.md`: isolated multi-agent fixture runner reference.
- `reference/vscode-extension.md`: VS Code syntax-highlighting extension install, local build, and release asset notes.
- `research/agent-sdk-pattern-survey.md`: survey of OpenAI Agents SDK, Google ADK, Claude Agent SDK, and Strands patterns.
- `examples/demo-agent-teams.md`: proposed realistic demo teams for fixtures and examples.
- `examples/fake-tools-and-data.md`: local fake-tool and fake-database fixture rules.
- `examples/incident-command-walkthrough.md`: runnable V1 fixture walkthrough.
- `../examples/README.md`: reusable pattern for public example projects.
- `../examples/incident-command/README.md`: beginner-facing Incident Command example guide.
- `../examples/multi-lens-research/README.md`: multi-lens research example guide.
- `../examples/market-research-brief/README.md`: document-driven market research example guide.
- `releasing.md`: tag-first release notes and PyPI publishing process.
- `decisions/open-questions.md`: decisions needed before or during the first implementation tranche.

## Documentation Rules

- Keep `AGENTS.md` as a routing map.
- Keep `VISION.md` as product intent, not implementation detail.
- Put concrete implementation decisions in the relevant topical document under this directory.
- When implementation changes the design, update the relevant doc in the same change.
- Prefer small, linked documents over a single large design note.
