# Multi-Lens Research

This example models a larger research team that maps evidence, evaluates it
through multiple specialist lenses, and synthesizes a source-backed brief.

## Team and Composition

`ResearchDirector` can delegate to:

- `EvidenceMapper`
- `TechnicalLensAnalyst`
- `PolicySafetyLensAnalyst`
- `CounterargumentAnalyst`
- `SynthesisWriter`

The named edges in `composition.contract` explicitly map typed inputs and prior
edge results. They represent model-selectable relationships. The separate
`MultiLensResearchRun` run spec verifies deterministic host-owned stage
ordering without turning the contract into a workflow engine.

## Shared Capabilities

`capabilities/research.contract` defines source search/fetch, evidence scoring,
citation formatting, and expert review once. Agents receive explicit grants to
the subset they need. Implementations are bound once under the OpenAI target.

## Honest Isolation

The `FreshResearchContext` profile declares context, capability, state,
filesystem, network, secret, and return-channel requirements independently. It
selects the in-process environment in the target profile.

The plan reports which dimensions the provider enforces, emulates, inherits,
or cannot support. In-process execution does not pretend to provide an OS or
network security boundary. A stronger required profile would need a stronger
environment provider and would otherwise fail closed.

## Assurance

`assurance.contract` requires specialist evidence before synthesis, defines an
evaluator-facing balanced-brief rubric, and declares a research latency budget.
The normalized trace joins those declarations to the exact composition and
capability events by stable semantic ID.

## Run the Offline Loop

```bash
pdm run python examples/multi-lens-research/data/seed.py
export CONTRACT4AGENTS_MULTI_LENS_DB="$PWD/examples/multi-lens-research/data/fixture.sqlite"
pdm run contract4agents check examples/multi-lens-research
pdm run contract4agents compile examples/multi-lens-research \
  --out .contract/build/multi-lens-research
pdm run contract4agents plan examples/multi-lens-research \
  --target openai --profile test \
  --out .contract/build/multi-lens-research/plan.json
pdm run contract4agents eval examples/multi-lens-research \
  --target openai --profile test
```

The test path is deterministic and requires no provider credentials. Review the
canonical IR and plan before the eval report: they show exactly what the
campaign was expected to execute and observe.

## Materialize

```python
from contract4agents import materialize

system = materialize(
    "examples/multi-lens-research",
    target="openai",
    profile="production",
)

director = system.agents["ResearchDirector"]
```

The host runs `director` through the normal SDK and retains deterministic
workflow, persistence, live source access, credentials, and review decisions.
