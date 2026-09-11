# Public Examples

Start with [Incident Command](incident-command/README.md). It shows how a
contract defines a team, binds its tools, and produces instructions and schemas.
The other examples add larger research teams and provider-native search.

These are repository examples. Run their commands after the repository setup
in the [README](../README.md). Their offline replay assesses supplied evidence;
it does not call a model or run the generated agents.

## Files You Will Use

Every public example follows the same structure:

```text
example-name/
  agents/                         portable agent definitions and grants
  capabilities/                   shared tools, datasources, external context
  types/                          portable structural types
  evals/                          named scenarios and expectations
  assurance.contract              controls, quality, operational controls
  composition.contract            named delegation and handoff edges
  contract4agents.targets.toml     target implementations and profiles
  eval-data.json                   deterministic offline replay evidence
```

Read `agents/` to see what each agent does, then follow its references to types
and capabilities. The target file selects the implementations. `evals/` states
the expectations; `eval-data.json` supplies the evidence used to assess them.

## Common Local Loop

From the repository root, substitute the example directory for `ROOT`:

```bash
pdm run python ROOT/data/seed.py
pdm run contract4agents check ROOT
pdm run contract4agents compile ROOT --out .contract/build/example
pdm run contract4agents plan ROOT --target openai --profile test \
  --out .contract/build/example/plan.json
pdm run contract4agents eval replay ROOT --target openai --profile test
pdm run contract4agents visualize ROOT --target openai --profile test \
  --out .contract/build/example/visualization
```

`seed.py` creates local fixture data. `check` validates the declarations and
bindings. `compile` writes schemas and instructions. `plan` reports how the
selected target represents the contract. `eval replay` assesses the supplied
output and events. `visualize` writes diagrams for inspection.

These commands require no provider credentials. Binding checks can import local
Python modules; imports execute module-level code. Generated `.contract/` output
is disposable.

Run `generate` separately only when an application imports generated types,
and select only the language targets it consumes. For example:

```bash
pdm run contract4agents generate ROOT --target python --out src/generated
```

## Incident Command

[Incident Command](incident-command/README.md) is the recommended first example.
It demonstrates:

- a shared tool granted to different agents with different authorization;
- explicit datasource and external-context origins;
- approval-derived controls;
- generated specialist delegations;
- post-run, quality, and operational assurance declarations.

## Multi-Lens Research

[Multi-Lens Research](multi-lens-research/README.md) demonstrates:

- a larger typed delegation graph;
- result mappings between specialist stages;
- an explicit multidimensional isolation profile;
- a run spec for host-owned deterministic workflow;
- source-backed synthesis controls.

## Market Research Brief

[Market Research Brief](market-research-brief/README.md) demonstrates:

- reusable document, current-fact, competitor, and citation tools;
- a provider-native web-search target binding;
- evaluator-only rubrics that do not leak into model instructions;
- freshness controls for claims drawn from dated documents.

## Reusing the Pattern

Copy semantic declarations, not provider wiring. For a new project:

1. define types and shared capabilities;
2. define agents and explicit per-agent grants;
3. define named composition edges and value mappings;
4. declare only controls and rubrics with clear assessment ownership;
5. create a target binding for the selected runtime;
6. inspect the plan before materialization;
7. run the resulting SDK agents from application code.

When useful, collect normalized traces and assess them against the same plan.

Host code should contain the real implementations, credentials, approval UI,
persistence, and deterministic workflow. It should not reconstruct the agent
configuration already present in the contracts.
