from contract4agents.type_refs import (
    canonical_type_name,
    is_bounded_numeric_type,
    is_builtin_type,
    is_literal_type,
    is_literal_union,
    literal_values,
    numeric_bounds,
    referenced_type_names,
)


def test_canonical_type_name_normalizes_nullable_and_collection_wrappers() -> None:
    assert canonical_type_name("IncidentContext?") == "IncidentContext"
    assert canonical_type_name("IncidentContext[]") == "IncidentContext"
    assert canonical_type_name("list[IncidentContext]") == "IncidentContext"
    assert canonical_type_name("list[IncidentContext?]") == "IncidentContext"


def test_canonical_type_name_normalizes_bounded_numeric_forms() -> None:
    assert canonical_type_name("int between 1 and 3") == "int"
    assert canonical_type_name("float between 0.1 and 0.9") == "float"
    assert numeric_bounds("int between 1 and 3") == ("int", 1.0, 3.0)
    assert is_bounded_numeric_type("float between 0.1 and 0.9")


def test_literal_type_detection_and_values() -> None:
    assert literal_values('"low" | "high"') == ["low", "high"]
    assert is_literal_type('"low" | "high"')
    assert is_literal_union('"low" | "high"')
    assert not is_literal_union('"low"')


def test_builtin_and_native_reference_detection() -> None:
    assert is_builtin_type("int?")
    assert is_builtin_type("list[AgentRef]")
    assert referenced_type_names("list[IncidentContext?]") == {"IncidentContext"}
    assert referenced_type_names("AgentRef[]") == set()
    assert referenced_type_names('"low" | "high"') == set()
    assert referenced_type_names("int between 1 and 3") == set()
