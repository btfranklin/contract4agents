# Grammar Reference

The implemented language is the portable V2 surface documented in
[Contract Language](../language/contract-language.md). A project may contain
`.contract` and `.eval` files.

Top-level declarations are:

- Native `type` declarations using `string`, `integer`, `float`, `boolean`,
  `datetime`, nullable `T?`, `list[T]`, and `map[string,T]`.
- Shared `tool` and typed `datasource` interfaces.
- `external_context` and multidimensional `isolation` requirements.
- Typed `agent` signatures with structured `use capability:` grants, explicit
  context requirements, `goal`, `description`, and `guidance`.
- Named `composition` edges with `delegate` or `handoff` mode and explicit input
  mappings.
- `control`, `quality`, and `operational_control` declarations.
- `eval` and `run_spec` declarations.

Implementation locators are never contract syntax. Python, TypeScript,
provider-hosted, remote, datasource, and external-context implementations belong
in target bindings.

Removed pre-contract-first forms are intentionally invalid and have no
compatibility aliases:

- `type ... from python`
- implementation-bearing datasource bodies
- `use tool|agent|datasource ... from ...`
- `use hosted_tool ...`
- agent-level `policy`, `success`, `host_context`, `guards`, `assertions`, and
  list-valued `composition`
- standalone `monitor` declarations
- `available`, `requires approval`, and `sandboxed` permission states
- `str`, `int`, `bool`, and `T[]` type spellings

The parser rejects removed structural forms. Generic assignment syntax is
parsed first so semantic analysis can report an exact unknown-attribute
diagnostic for removed agent attributes.
