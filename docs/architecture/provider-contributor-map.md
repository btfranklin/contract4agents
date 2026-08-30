# Provider Contributor Map

Use this map when you add or change a target provider. Keep provider differences
explicit. Do not create a shared provider framework only because two providers
have similar code.

## Change Route

| Responsibility | Location | Purpose |
| --- | --- | --- |
| Portable meaning | `docs/language/`, `ast.py`, `semantic_checks/`, and `ir/` | Define and validate contract-owned semantics. |
| Target binding | `target_bindings/` | Load implementation locators, profiles, models, and provider options. |
| Planning support | `adapters/_<provider>.py` | State exact, host-enforced, emulated, degraded, and unsupported mappings. |
| Provider discovery | `adapters/_registry.py` | Connect the adapter name to planning and materialization factories. |
| Native construction | `materialization/_<provider>.py` | Build normal provider SDK objects and wire the native graph. |
| Native validation | `materialization/_<provider>_validation.py` | Read back native configuration and compare it with the immutable plan. |
| Runtime evidence | `tracing/_<provider>.py` and related provider modules | Normalize provider events without changing their meaning. |
| Public behavior | `docs/reference/<provider>-adapter.md` | Describe supported mappings, limits, and host duties. |
| Provider tests | `tests/offline/materialization/test_<provider>_materialization.py` | Test provider-specific graph construction and SDK behavior. |
| Reusable test support | `tests/support/<provider>.py` | Hold fake SDK boundaries and provider project builders. |

`<provider>` is `openai`, `strands`, or `google_adk` for the built-in targets.

## Required Sequence

1. Define portable semantics only when the contract language must own the
   behavior.
2. Validate target binding shapes without invoking host business code.
3. Declare contextual planning support in the provider adapter.
4. Register the provider factory.
5. Construct native SDK objects from the immutable materialization plan.
6. Read back the completed native graph in the provider validation module.
7. Emit and normalize evidence without treating missing evidence as success.
8. Update provider tests and the provider reference document.

## Boundary Rules

- Planning must finish before native objects exist.
- Required degraded or unsupported semantics must stop the lifecycle.
- Native construction and two-pass graph wiring stay in the provider
  materializer.
- Schema and configuration readback stay in the provider validation module.
- Provider-neutral evidence models stay in shared materialization and tracing
  modules.
- Provider test modules must import reusable fakes from `tests/support/`.
- A provider implementation must not redefine portable contract semantics.
- The host continues to own credentials, approval decisions, persistence,
  deterministic workflow, deployment, and external services.

## Validation

Run the provider materialization tests first. Then run the full repository gate:

```bash
pdm run validate
```

Run `pdm run smoke:cli` when the change affects the public lifecycle or example
targets. Live provider checks remain opt-in.
