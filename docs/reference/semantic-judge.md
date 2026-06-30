# Semantic Judge Reference

Semantic evals are V1 scope.

The first judge adapter is OpenAI. The eval runner reports semantic checks separately from deterministic output checks and trace spies.

If no judge is configured, semantic checks are reported as skipped instead of passing silently.

## Live OpenAI Check

The normal test suite does not call external APIs. To verify the real OpenAI semantic judge path, run:

```bash
CONTRACT4AGENTS_RUN_OPENAI_LIVE=1 pdm run test:openai-live
```

The test loads `OPENAI_API_KEY` from the process environment, falling back to the ignored local `.env` file when present. It never reads or prints the key value. Set `CONTRACT4AGENTS_OPENAI_JUDGE_MODEL` to override the default judge model.

This check uses the Incident Command fixture as the scenario source. It asks the judge to verify that a realistic incident brief names the expected deploy, cites evidence, describes impact, and does not claim an approval-gated status-page publish happened.
