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

## System Review Page

The page opens with a calm system map rather than the full semantic inventory.
It shows every agent and every delegation or handoff. Select an agent to focus
the map on its meaningful neighborhood:

- collaborators and composition direction;
- tools and their approval requirements;
- context and its declared source;
- controls and their current assurance status.

The inspector explains the selected agent in human language. Exact guidance,
schemas, semantic IDs, model options, and evidence remain available under
**Agent technical details**. **Show whole system** returns to the overview in
one action.

The page uses a precomputed, deterministic layout. Its inline SVG, styles, data,
and interaction code are embedded in `index.html`; opening the artifact makes no
network requests.

## Evidence Progression

With source alone, the page emphasizes declared canonical structure. The
evidence progression then moves through four distinct questions:

- **Declared:** what does the contract require?
- **Planned:** how will a target and profile implement it?
- **Observed:** what happened in a normalized run trace?
- **Assured:** what do the assessed controls establish?

Add `--target` and `--profile` together for planned mappings. Add `--trace` for
observed events; when both plan and trace are present, controls are assessed for
the assured layer. Selecting an unavailable stage preserves the system geometry
and explains the missing evidence instead of showing an empty graph.

One review strip prioritizes the next consequential item, such as an unverified
required control or a plan without runtime evidence. Normal missing inputs are
evidence gaps. Warnings are reserved for inconsistent evidence, such as an
assurance result that references a missing trace event.

Counts in the progression describe the whole graph in the system overview and
the selected semantic neighborhood in agent focus. Use `diff` for change-review
artifacts. Every layer preserves the same semantic IDs and digests.

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

## Raw Artifacts

`graph.json` remains the complete, stable review graph. The HTML derives its
smaller presentation model from that graph at generation time; it does not add
another semantic source of truth. `graph.mmd` likewise retains the complete
graph even though the default HTML map shows only the agent team.

The page keeps digests, semantic entity and relationship counts, and Mermaid
source under the closed **Technical details** disclosure. These values support
precise review without dominating initial orientation.

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
- missing expected event types.

The underlying source remains `.contract` and `.eval` files plus target
bindings. Generated plans, traces, and assurance results are evidence inputs,
not editable configuration.
