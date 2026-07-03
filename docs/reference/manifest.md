# Manifest Reference

The provider-neutral manifest is the compiler's stable machine-readable output.

Each agent manifest includes:

- agent identity and description
- source path for the agent declaration
- typed inputs, including `python_ref` when a type is imported from Pydantic
- output type, JSON Schema reference, and imported-model metadata when present
- host-orchestrated context types declared with `host_context`
- tools with permission state
- subagents
- datasources
- policy and success criteria
- routes and composition declarations
- guards and assertions

Raw guard strings stay in each manifest for review. The compiler also emits
`guards/guard-plan.json` for host and adapter enforcement metadata.

The compiler emits canonical schemas under `schemas/*.json` and type source
metadata under `types/type-bindings.json`. Imported Pydantic model references are
metadata; JSON Schema remains the canonical interchange artifact.

`host_context` entries record typed values the host is expected to supply while
wiring child-agent calls. They participate in static child-context
satisfiability checks but do not define workflow ordering or execution.

SDK adapters consume this manifest and emit adapter-specific objects or warnings.
