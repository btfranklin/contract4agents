# Manifest Reference

The provider-neutral manifest is the compiler's stable machine-readable output.

Each agent manifest includes:

- agent identity and description
- typed inputs
- output type and JSON Schema reference
- tools with permission state
- subagents
- datasources
- policy and success criteria
- routes and composition declarations
- guards and assertions

SDK adapters consume this manifest and emit adapter-specific objects or warnings.
