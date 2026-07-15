# Semantic Judge Reference

Semantic judges assess named `quality` rubrics during eval campaigns. They are
evaluation providers, not model-facing agent configuration.

```contract
quality evidence_backed for ResearchLead:
    rubric = "Every material claim is supported by current cited evidence."
    audience = [evaluator, reviewer]
```

An eval opts into the rubric with `expect quality(evidence_backed)`.

## Required Evidence

A judge decision records:

- passed or violated status and a reason;
- optional numeric score;
- rubric semantic ID;
- judge provider and version;
- evidence references;
- campaign contract and plan digests.

For live judges, integrations should additionally retain the judge model,
rubric/prompt digest, and sampling configuration in provider evidence. The
rubric must not be injected into the agent-under-test's model prompt.

No configured decision, a judge error, malformed output, or missing identity
produces `unverified`. Semantic work is never reported as skipped-and-passed.

## Deterministic and Live Providers

`FileEvalProvider` reads deterministic judge decisions from `eval-data.json`.
A custom `EvalProvider.judge` implementation can call OpenAI or another judge
service and return `JudgeDecision`.

Normal repository validation does not call external APIs. Contract4Agents does
not currently ship a provider-specific live semantic judge. Applications can
implement the provider-neutral `EvalProvider.judge` protocol; provider absence,
errors, and malformed decisions remain `unverified`. The opt-in OpenAI agent
smoke test validates native execution and trace correlation, not semantic-judge
quality.
