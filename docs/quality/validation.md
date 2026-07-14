# Validation And Quality Gates

This document is the operational map for validating Contract4Agents changes. Keep command behavior details in `docs/reference/cli.md`; keep fixture-runner details in `docs/reference/test-fixtures.md`.

## Default Local Validation

Run the composite gate before handing off code changes:

```bash
pdm run validate
```

That runs:

- `pdm run lint`: Ruff over `src`, `tests`, and `examples`.
- `pdm run typecheck`: strict mypy over `src`.
- `pdm run docs-check`: required-doc and markdown-link checks.
- `pdm run test`: offline unit and integration tests.

Skipped live OpenAI tests are expected in the default gate unless the explicit live-test environment flags are set.

## Packaging Validation

Run packaging validation when changing `pyproject.toml`, `README.md`, `LICENSE`, build configuration, or public package files:

```bash
pdm build
```

The source distribution should not include generated `*.egg-info` directories or the repository-level `examples/` projects. The project uses `pdm-backend` explicitly so package metadata is generated during build without publishing stale local build artifacts.

Package versions are derived from Git tags through `pdm-backend` SCM versioning. Use semantic version tags in the form `vX.Y.Z`; do not hand-edit a static package version in `pyproject.toml`.

## VS Code Extension Validation

Run the VS Code extension package gate when changing `editors/vscode`,
`.github/workflows/vscode-extension.yml`, or release asset handling:

```bash
npm --prefix editors/vscode ci
npm --prefix editors/vscode test
npm --prefix editors/vscode run package
```

Generated `.vsix` files are release assets, not Python package files. They
should stay untracked locally.

## Release Pipeline

The repository follows the same release path as the sibling public Python packages:

- `.github/workflows/python-package.yml` runs the full local validation gate and builds the package on push and pull request.
- `.github/workflows/vscode-extension.yml` builds the VS Code syntax-highlighting VSIX on push and pull request.
- `.github/workflows/create-draft-release.yml` creates draft release notes when a `v*.*.*` tag is pushed.
- `.github/workflows/python-publish.yml` publishes to PyPI through Trusted Publishing when a GitHub release is published.

Release setup requires the GitHub repository secret `OPENAI_API_KEY` for draft release notes, a GitHub environment named `release`, and a PyPI Trusted Publisher entry for the `python-publish.yml` workflow with the `release` environment. The release-note workflow should draft the notes; do not manually write release notes before the tag workflow runs.

## CLI Smoke Path

Use this path when changing parser, semantic analyzer, compiler, visualization,
public examples, or README command examples:

```bash
pdm run smoke:cli
```

Generated `.contract/` output is local state and should stay untracked.
Use `pdm run test:agent-fixture` or `pdm run contract4agents eval tests/fixtures/contract_projects/ops-desk-lab`
when changing internal fixture edge-case behavior.
Use `pdm run contract4agents check tests/fixtures/contract_projects/host-drift --strict-drift`
when changing capability registry or host-code drift behavior.

## Live OpenAI Checks

The normal validation suite does not call external APIs. Run live checks only when changing OpenAI client setup, semantic judging, or OpenAI Agents SDK execution behavior:

```bash
CONTRACT4AGENTS_RUN_OPENAI_LIVE=1 pdm run test:openai-live
CONTRACT4AGENTS_RUN_OPENAI_AGENT_LIVE=1 pdm run test:openai-agent-live
```

These checks require `OPENAI_API_KEY` in the process environment or the ignored local `.env` file. Do not treat skipped live tests as proof that live OpenAI behavior was exercised.

## Documentation Freshness

`pdm run docs-check` protects the main repo map:

- required top-level and docs-system files must exist;
- normal markdown links to local `.md` files must resolve;
- markdown paths listed in `docs/index.md` must resolve.

When adding a new durable design, reference, quality, or operations document, link it from `docs/index.md` so coding agents can discover it.
