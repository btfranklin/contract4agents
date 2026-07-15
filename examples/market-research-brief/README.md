# Market Research Brief

This example produces a current, source-backed market opportunity report from
dated documents, refreshed facts, competitor evidence, customer signals, and
provider-native web search.

## Team

`MarketResearchLead` delegates to document, current-truth, competitor, customer
signal, and report-writing specialists through named typed composition edges.
The materializer constructs those native relationships from contracts; the host
does not provide parallel agent factories or handoff objects.

## Portable Capabilities, Target-Specific Implementations

`capabilities/market.contract` declares document, current-fact, competitor,
citation, and web-search interfaces once. Most OpenAI target bindings point to
local Python functions. `web.search` instead selects the provider-native web
search implementation:

```toml
[targets.openai.tools."web.search"]
provider = "openai"
tool = "web_search"
search_context_size = "medium"
```

The portable contract does not know whether another target implements the same
capability through a host function, MCP server, remote service, or provider
tool.

## Assurance

The explicit control requires current-fact evidence when claims depend on dated
documents. Three named quality rubrics assess thesis clarity, source separation,
and balance. Their evaluator/reviewer audience keeps the rubrics out of model
instructions.

## Run the Offline Loop

```bash
pdm run python examples/market-research-brief/data/seed.py
export CONTRACT4AGENTS_MARKET_RESEARCH_DB="$PWD/examples/market-research-brief/data/fixture.sqlite"
pdm run contract4agents check examples/market-research-brief
pdm run contract4agents compile examples/market-research-brief \
  --out .contract/build/market-research-brief
pdm run contract4agents plan examples/market-research-brief \
  --target openai --profile test \
  --out .contract/build/market-research-brief/plan.json
pdm run contract4agents eval examples/market-research-brief \
  --target openai --profile test
```

The test campaign uses deterministic local evidence and normalized trace data,
so it needs no provider credentials. The plan still records that `web.search`
is provider-hosted and which telemetry and host obligations would apply to a
real run.

## Materialize

```python
from contract4agents import materialize

system = materialize(
    "examples/market-research-brief",
    target="openai",
    profile="production",
)

lead = system.agents["MarketResearchLead"]
```

The host executes the normal SDK object and owns live data credentials,
approval decisions, persistence, and deterministic application workflow.
