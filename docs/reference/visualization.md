# Visualization Reference

Visualization is a read-only review surface over derived Contract4Agents
artifacts. It never becomes a second source of truth.

```bash
contract4agents visualize agent_contracts \
  --target openai --profile production \
  --trace run.trace.jsonl \
  --out .contract/build/visualization
```

The static output contains:

- `graph.json`: deterministic review data.
- `graph.mmd`: Mermaid source.
- `index.html`: standalone interactive review page.

## Layered CLI View

With source alone, the page renders declared canonical structure:

- agents, capabilities, grants, and composition;
- input/output types and context sources;
- controls, quality, and isolation requirements.

Add `--target` and `--profile` together for planned mappings. Add `--trace` for
observed events; when both plan and trace are present, controls are assessed for
the assured layer. Use `diff` for change-review artifacts. Every layer preserves
the same semantic IDs and digests.

## Layered Python API

The graph builder also accepts reviewed plan, normalized trace, and control
results to construct all four truth layers without collapsing them:

```python
from contract4agents import compile_project
from contract4agents.visualization import build_visualization_graph

artifacts = compile_project("agent_contracts")

graph = build_visualization_graph(
    artifacts.ir,
    project_root="agent_contracts",
    plan=plan,
    trace=trace,
    control_results=control_results,
)
```

The layered graph separates:

- **declared** contract structure;
- **planned** models, bindings, mechanisms, obligations, and caveats;
- **observed** normalized events and provider correlation;
- **assured** passed, violated, and unverified control status.

Omitted inputs remain visibly unavailable. A planned or assessed value never
overwrites what was declared, and missing runtime evidence never becomes an
observed fact.

## Semantic Joins

Nodes and edges use kind-qualified semantic IDs, contract digest, and plan
digest. This prevents a display label or filename from being mistaken for
identity and makes contract/plan changes visible across layers.

High-value review highlights include:

- capability access and authorization changes;
- approval enforcement and evidence gaps;
- context exposure and audience changes;
- delegation versus handoff behavior;
- isolation requested versus actually enforced;
- degraded or unsupported plan mappings;
- controls that are violated or unverified;
- missing expected telemetry.

The underlying source remains `.contract` and `.eval` files plus target
bindings. Generated plans, traces, and assurance results are evidence inputs,
not editable configuration.
