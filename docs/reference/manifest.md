# Manifest Reference

The provider-neutral manifest is the compiler's stable machine-readable output.

Each agent manifest includes:

- agent identity and description
- source path for the agent declaration
- typed inputs, including `python_ref` when a type is imported from Pydantic
- output type, JSON Schema reference, and imported-model metadata when present
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

SDK adapters consume this manifest and emit adapter-specific objects or warnings.
