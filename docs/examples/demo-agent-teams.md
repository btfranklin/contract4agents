# Demo Agent Teams

The public examples show how one contract project defines a team, its shared
tools, typed results, and expectations. Each includes local fake data and
recorded evidence for an offline replay. Replay does not run the agents.

For setup commands, start with the [public examples guide](../../examples/README.md).
Use this page to choose an example or design a new fixture.

## Choose an Example

| Example | Task | Main lesson |
| --- | --- | --- |
| [Incident Command](../../examples/incident-command/README.md) | Investigate an incident and prepare a brief. | Declare a shared tool once, grant different access to each agent, and generate specialist relationships. |
| [Multi-Lens Research](../../examples/multi-lens-research/README.md) | Combine evidence, technical analysis, policy analysis, and counterarguments. | Map typed inputs and results between specialists; describe expectations for a host-run workflow. |
| [Market Research Brief](../../examples/market-research-brief/README.md) | Combine documents, dated facts, competitor records, and customer signals. | Bind a portable search capability to a provider tool and assess source freshness. |

Start with Incident Command. Its smaller team makes the relationship between
contracts, bindings, generated instructions, and schemas easier to inspect.
Then use Multi-Lens Research for composition and Market Research Brief for
provider-tool differences. Each example's contract files contain the current
agent, type, and capability definitions.

## What the Examples Should Teach

Readers should be able to locate:

- The agent's inputs, output, goal, and instructions.
- Its tool grants and named specialist relationships.
- The Python or provider implementation selected by the target binding.
- The generated instructions and schemas.
- The expectations assessed by the replay, and the evidence supplied to it.

The examples also show approval declarations, context origins, quality rubrics,
and controls. These are useful extensions of the agent definition; readers do
not need to understand every assessment feature before using an agent.

## Local Tools and Data

Use ordinary Python functions backed by fake local data. Keep IDs and timestamps
stable so results can be reproduced. Record simulated side effects instead of
writing to external services. Seed scripts must work without live credentials
or network access.

Keep evaluator-only facts separate from data available to the agent. In a live
test, the agent must discover facts through its declared tools and context.
In replay, the fixture supplies output and recorded events directly to the
assessor. Do not describe those supplied events as proof of live agent behavior.

See [Deterministic Eval Data](fake-tools-and-data.md) for the replay file format,
audience channels, and negative cases.

## Fixture Review

Before adding an example, check that:

1. Its task explains a useful agent-definition pattern.
2. Contracts own the types, grants, and composition; bindings select their
   implementations without repeating those declarations.
3. Local fake tools return realistic, repeatable data.
4. The guide identifies what each command produces and whether it runs an agent.
5. Replay expectations cover useful failures as well as passing evidence.
6. Provider limits are stated where they affect the example.

Keep detailed inventories in the contract files. The guide should explain the
choices and help the reader inspect the generated result.
