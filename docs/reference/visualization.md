# Visualization Reference

The visualization command generates static review artifacts for a Contract4Agents project.

```bash
pdm run contract4agents visualize [ROOT] --out .contract/build/visualization
```

Outputs:

- `graph.json`: deterministic project graph data.
- `graph.mmd`: Mermaid flowchart source.
- `index.html`: static HTML page with Mermaid rendering, agent detail drill-in, and focused agent diagrams.

Visualization is read-only generated output. The source of truth remains the `.contract` and `.eval` files.

V1 renders configured/static relationships only:

- agent-to-agent capability declarations
- agent-to-tool capability declarations
- agent-to-datasource declarations
- datasource required and produced types
- agent input and output types
- eval and monitor targets

Route and composition metadata is shown in agent details, but V1 does not infer natural-language routing strings into graph edges.

Selecting an agent in the HTML page swaps the overview diagram for a pre-rendered focused diagram. Focused diagrams include the selected agent, directly declared neighbors, and datasource type dependencies for datasources adjacent to the selected agent.
