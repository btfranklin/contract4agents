# Agent Instructions

This repository is the design and implementation home for Contract4Agents, a typed declarative language for agent contracts.

## Repo Map

- `README.md` is the public/user front door; this file is the coding-agent operating map.
- Start with `VISION.md` for the product and architecture intent.
- Use `docs/architecture/semantic-model.md` for the accepted
  implementation decisions.
- Use `docs/index.md` as the documentation entry point.
- Language syntax and semantics live in `docs/language/contract-language.md`.
- Practical adoption guidance lives in `docs/tutorials/using-contract4agents-with-an-agent-app.md`.
- Runtime context and datasource design live in `docs/runtime/context-and-datasources.md`.
- Compiler outputs and static checks live in `docs/compiler/compiler-outputs.md`.
- Eval, control, and assurance design lives in `docs/evaluation/evals-controls-assurance.md`.
- Validation, release checks, and live-test boundaries live in `docs/quality/validation.md`.
- Current SDK research lives in `docs/research/agent-sdk-pattern-survey.md`.
- VS Code syntax-highlighting extension guidance lives in `docs/reference/vscode-extension.md`.
- Demo team planning lives in `docs/examples/demo-agent-teams.md`.
- Fake local tool and data fixture rules live in `docs/examples/fake-tools-and-data.md`.
- Public example structure lives in `examples/README.md`.
- The beginner-facing Incident Command guide lives in `examples/incident-command/README.md`.
- Additional public example guides live in `examples/multi-lens-research/README.md`
  and `examples/market-research-brief/README.md`.
- Release process and tag-first release notes flow live in `docs/releasing.md`.
- Open product and architecture questions live in `docs/decisions/open-questions.md`.

## Package Management

- Use PDM for all Python package and environment management.
- Do not use `pip` directly for project dependency management.
- When adding dependencies, check the latest available version and express the dependency with a lower bound such as `package>=x.y.z`.
- Do not pin exact versions unless a specific compatibility issue requires it.

## Documentation Rules

- Keep this file short. It is a map, not the system of record.
- Put durable design decisions in the relevant topical docs under `docs/`.
- When a design question is unresolved, update `docs/decisions/open-questions.md` instead of burying the uncertainty in prose.
- Prefer examples that can later become parser fixtures, runtime fixtures, or eval cases.

## Validation

- Run `pdm run validate` before handing off code changes unless the task is docs-only and the scope clearly does not affect behavior.
- Run `pdm build` when changing packaging metadata, README/license files, or build configuration.
- Live OpenAI checks are opt-in; see `docs/quality/validation.md` before treating skipped live tests as coverage.

## Current State

- This repository implements the contract-first path: canonical IR, target
  bindings and planning, portable type generation, native OpenAI
  materialization, multidimensional isolation planning, contract-bound traces,
  eval campaigns, shared control assessment, assurance bundles, semantic diffs,
  visualization, and the public CLI.
