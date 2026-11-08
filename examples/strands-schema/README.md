# Native Strands schema output

This example uses the normal Strands agent loop with a bound source tool and
native strict Chat Completions output. C4A generates the output schema. The
model factory selects Bedrock Runtime and maximum Kimi reasoning. The host
validates the final JSON against all contract bounds.

From the repository root, check and plan without model calls:

```bash
pdm run contract4agents check examples/strands-schema
pdm run contract4agents plan examples/strands-schema --target strands --profile test
```

A live invocation requires AWS credentials, access to the selected model, and
permission to use the Bedrock bearer-token route. Use a permitted development
account. This command makes billable model calls:

```bash
pdm run python examples/strands-schema/run.py --live "Summarize synthetic:one."
```

The native Strands provider generates short-lived AWS tokens from its credential
chain. No static API key belongs in the target bindings. The example disables
OpenAI client retries. It does not add a host retry or fallback policy.

Read the [Strands target reference](../../docs/reference/strands-adapter.md)
for output modes, native schema evidence, reasoning, and host trace capture.
