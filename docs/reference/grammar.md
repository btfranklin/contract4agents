# Grammar Reference

The implemented language is the portable surface documented in
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

Generic assignment syntax is parsed before semantic analysis. Semantic checks
then validate each declaration against the attributes defined by the current
language surface and report unknown attributes with the accepted set.
