# Grammar Reference

The implemented language is the portable surface documented in
[Contract Language](../language/contract-language.md). A project may contain
`.contract` and `.eval` files.

Top-level declarations are:

- Native structural `type` declarations and closed string `enum` declarations,
  using `string`, `integer`, `float`, `boolean`, `datetime`, nullable `T?`,
  `list[T]`, bounded `list[T](min_items=N,max_items=N)`, and
  `map[string,T]`. List bounds are non-negative integers; one or both bounds
  may be present.
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

The canonical type spelling places a list constraint block before `?`, and
orders both bounds as `min_items` then `max_items`. Primitive string and numeric
constraints remain separate from list cardinality constraints. Map constraints
are not part of the portable grammar. Datetimes use the shared RFC 3339 subset
with a literal `T` and a required timezone offset.

Generic assignment syntax is parsed before semantic analysis. Semantic checks
then validate each declaration against the attributes defined by the current
language surface and report unknown attributes with the accepted set.

Enum members use an indented block of quoted strings:

```contract
enum VerificationStatus:
    "accepted"
    "follow_up"
    "failed"
```

An enum must contain at least one nonempty, unique member. Enum and structural
type names occupy the same namespace.
