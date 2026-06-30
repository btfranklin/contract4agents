# Accepted Decisions

This document records decisions that are no longer open. Update it when a design choice is accepted so implementation work does not reopen settled scope by accident.

## 2026-05-15: Provider-Neutral Manifest First

Decision: Contract4Agents compiles to a provider-neutral manifest before compiling to any SDK-specific adapter.

Rationale: The SDK survey shows common concepts across OpenAI Agents SDK, Google ADK, Claude Agent SDK, and Strands, but no single SDK object model captures all of them cleanly. The manifest is the stable Contract4Agents target; SDK adapters are downstream projections.

Implications:

- Language semantics should not mirror one SDK too tightly.
- Adapter support should be checked through a capability matrix.
- Adapter-specific limitations should become warnings or errors, not hidden behavior changes.

## 2026-05-15: OpenAI First Execution Adapter

Decision: OpenAI Agents SDK is the first real execution adapter after the provider-neutral manifest is usable.

Rationale: OpenAI maps cleanly to Contract4Agents' initial concepts: named agents, instructions, tools, context, handoffs, guardrails, output schemas, results, and traces.

Implications:

- The first adapter conformance tests should target OpenAI.
- Contract4Agents still preserves Google ADK, Claude Agent SDK, and Strands patterns in the manifest.
- OpenAI-specific details belong in the adapter layer, not in core language semantics.

## 2026-05-15: OpenAI First Semantic Eval Judge

Decision: OpenAI is the first semantic eval judge adapter.

Rationale: Semantic evals are part of V1 scope, and using one concrete judge first makes the eval pipeline testable without blocking on a provider abstraction for every model vendor.

Implications:

- Semantic eval syntax, manifest representation, and reporting are V1 scope.
- The first working judge implementation targets OpenAI.
- The judge interface should remain adapter-shaped so other providers can be added.

## 2026-05-15: Lark Parser

Decision: Use Lark for the first Python parser.

Rationale: Contract4Agents needs a real grammar, source spans, useful diagnostics, and fast iteration. Lark is a pragmatic fit for the first implementation.

Implications:

- Lark parse trees are internal parser machinery.
- The compiler-facing representation remains the typed AST dataclasses.
- Parser code is split by grammar, tree-to-AST transformation, source value helpers, expression evaluation, and expression reference extraction rather than by adding a second AST model or parser framework.

## 2026-05-15: JSON Schema Canonical Type Interchange

Decision: JSON Schema is the canonical interchange format for `.contract` type declarations.

Rationale: JSON Schema is the best common denominator across the surveyed SDKs. OpenAI accepts JSON-Schema-compatible output types, Google ADK maps through schema objects or Pydantic, Claude Agent SDK uses JSON Schema for output format, and Strands maps through Pydantic or Zod-style schemas.

Implications:

- Contract4Agents source should not require Python model imports for core type declarations.
- Pydantic and Zod are adapter bindings or implementation conveniences, not the core type system.
- Compiler output should include JSON Schema for declared return types and relevant structured context.

## 2026-05-15: Click CLI

Decision: Use `click` for the CLI and expose the command as `contract4agents`.

Implications:

- `pdm run contract4agents --help` is part of Milestone 0 validation.
- CLI examples should use `pdm run contract4agents ...`.

## 2026-05-15: Trace Storage Starts Local

Decision: Start with local JSONL trace files for recorded runs.

Rationale: JSONL is inspectable and easy to diff.

## 2026-05-15: Demo Teams

Decision: Use three moderately realistic demo teams rather than the original customer-support sketch:

- `Incident Command`
- `Revenue Resolution`
- `Market Research Brief`

Rationale: Together they exercise read-only investigation, guarded financial side effects, research quality, semantic evals, trace spies, approvals, hidden state, and multiple composition styles.

## 2026-05-15: Incident Command First Fixture

Decision: `Incident Command` is the first concrete fixture to scaffold.

Rationale: It should be the easiest of the three to test and experiment with because it is mostly read-only, can use deterministic seeded incidents, and can validate whether agents discover hidden causes through local fake tools.

Implications:

- Parser and semantic analyzer fixtures should start with `Incident Command`.
- The first local fake tools and fake database should support incident logs, deploys, metrics, and status-page draft behavior.

## 2026-05-15: Local Fake Tools And Fake Data

Decision: Demo teams use local Python fake tools backed by fake local data, not MCP servers, remote connectors, or external APIs.

Rationale: The tools should be real enough to exercise tool schemas, execution, traces, hidden data, and evals, but fake enough to be deterministic, local, safe, and credential-free.

Implications:

- Fake tools should live in local Python modules.
- Fake data should be seeded locally, likely in SQLite.
- Eval scenarios can hide known causes or expected discoveries in the data, then verify agents find them through normal tool use.
- MCP and remote connector adapters can be tested later against the same manifest concepts.

## 2026-05-15: Datasource Return Contracts

Decision: datasources may return arbitrary values only when a renderer is supplied; otherwise they must return `ContextValue`.

Rationale: this preserves runtime metadata and rendered model context while keeping simple fixture resolvers ergonomic.

## 2026-05-15: Approval Callback Model

Decision: V1 approvals use a runtime callback interface and normalized approval trace events.

Rationale: approval gates are core safety behavior, and host applications own their own approval UI.

## 2026-05-15: Conventional Project Layout

Decision: V1 supports a conventional generated layout first: `agents/`, `evals/`, `types/`, `tools/`, `datasources/`, and `data/`.

Rationale: conventions make parser fixtures, runtime fixtures, docs, and agent runs easier to inspect.

## 2026-05-15: Inline Capability Imports

Decision: V1 uses inline `use ... from ...` declarations inside agents.

Rationale: inline declarations make the trust boundary and agent capability scope obvious.

## 2026-05-15: Prompt Asset Handling

Decision: authored prompt templates should use promptdown, while generated instructions are emitted as Markdown/text artifacts.

Rationale: promptdown remains the source format for authored prompts, and compiled instructions stay easy to inspect and diff.
