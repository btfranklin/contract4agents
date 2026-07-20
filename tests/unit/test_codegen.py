from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

import pytest

from contract4agents.codegen import (
    GENERATOR_VERSION,
    PYDANTIC_MODELS_PATH,
    TYPESCRIPT_TYPES_PATH,
    ZOD_SCHEMAS_PATH,
    CodeGenerationError,
    GeneratedCodeStaleError,
    generate_code,
    generate_pydantic_models,
    generate_typescript_types,
    generate_zod_schemas,
    stale_generated_paths,
    write_generated_code,
)
from contract4agents.ir import (
    CanonicalIR,
    EnumIR,
    TypeFieldIR,
    TypeIR,
    contract_digest,
    parse_type_ref,
    semantic_id,
)


def test_generate_code_emits_stable_paths_headers_and_dependency_order() -> None:
    ir = _portable_ir(reverse=True)

    generated = generate_code(ir, targets=("typescript", "python"))

    assert generated.generator_version == GENERATOR_VERSION
    assert generated.contract_digest == contract_digest(ir)
    assert tuple(generated.files) == (
        PYDANTIC_MODELS_PATH,
        TYPESCRIPT_TYPES_PATH,
        ZOD_SCHEMAS_PATH,
    )
    for source in generated.files.values():
        assert f"codegen v{GENERATOR_VERSION}" in source
        assert contract_digest(ir) in source
        assert "DO NOT EDIT" in source
        assert source.endswith("\n")

    python_source = generated.files[PYDANTIC_MODELS_PATH]
    typescript_source = generated.files[TYPESCRIPT_TYPES_PATH]
    assert python_source.index("class Address") < python_source.index("class Incident")
    assert typescript_source.index("interface Address") < typescript_source.index("interface Incident")

    same_semantics = _portable_ir(reverse=False)
    assert generate_code(same_semantics, targets=("python", "typescript")).files == generated.files


def test_generate_code_emits_only_selected_targets() -> None:
    ir = _portable_ir()

    python = generate_code(ir, targets=("python",))
    typescript = generate_code(ir, targets=("typescript",))
    combined = generate_code(ir, targets=("python", "typescript", "python"))

    assert tuple(python.files) == (PYDANTIC_MODELS_PATH,)
    assert tuple(typescript.files) == (TYPESCRIPT_TYPES_PATH, ZOD_SCHEMAS_PATH)
    assert tuple(combined.files) == (
        PYDANTIC_MODELS_PATH,
        TYPESCRIPT_TYPES_PATH,
        ZOD_SCHEMAS_PATH,
    )

    with pytest.raises(CodeGenerationError, match="CGEN003.*At least one generation target"):
        generate_code(ir, targets=())
    with pytest.raises(CodeGenerationError, match="CGEN003.*Unknown generation target: java"):
        generate_code(ir, targets=("java",))


def test_pydantic_generation_covers_portable_types_defaults_and_forward_refs() -> None:
    ir = _portable_ir()

    source = generate_pydantic_models(ir)

    assert "from datetime import datetime" in source
    assert 'model_config = ConfigDict(extra="forbid")' in source
    assert "address: Address" in source
    assert "aliases: list[str] = []" in source
    assert "labels: dict[str, str]" in source
    assert "score: float | None = None" in source
    assert "active: bool = True" in source
    assert "opened_at: datetime" in source
    assert "for _model in (Address, Incident):" in source

    namespace: dict[str, Any] = {"__name__": "generated_contract_models"}
    exec(compile(source, "<generated>", "exec"), namespace)
    incident_type = namespace["Incident"]
    value = incident_type(
        incident_id="inc-1",
        address={"city": "Phoenix"},
        labels={"severity": "high"},
        opened_at="2026-07-15T12:00:00Z",
    )
    assert value.address.city == "Phoenix"
    assert value.aliases == []
    assert value.active is True


def test_recursive_pydantic_models_rebuild_after_all_classes_exist() -> None:
    node = TypeIR(
        semantic_id("type", "Node"),
        "Node",
        (
            TypeFieldIR("name", parse_type_ref("string")),
            TypeFieldIR("children", parse_type_ref("list[Node]"), has_default=True, default=[]),
        ),
    )
    source = generate_pydantic_models(CanonicalIR.create(types=(node,)))
    namespace: dict[str, Any] = {"__name__": "generated_recursive_models"}

    exec(compile(source, "<generated>", "exec"), namespace)

    value = namespace["Node"](name="root", children=[{"name": "child"}])
    assert value.children[0].name == "child"


def test_nullable_field_without_explicit_default_is_optional_in_every_target() -> None:
    record = TypeIR(
        semantic_id("type", "Record"),
        "Record",
        (TypeFieldIR("note", parse_type_ref("string?")),),
    )
    ir = CanonicalIR.create(types=(record,))
    python = generate_pydantic_models(ir)
    typescript = generate_typescript_types(ir)
    zod = generate_zod_schemas(ir)
    namespace: dict[str, Any] = {"__name__": "generated_nullable_models"}

    exec(compile(python, "<generated>", "exec"), namespace)

    assert namespace["Record"]().note is None
    assert "note: str | None = None" in python
    assert "note?: string | null;" in typescript
    assert "note: z.string().nullable().optional().default(null)," in zod


def test_typescript_and_zod_generation_cover_all_portable_type_forms() -> None:
    ir = _portable_ir()

    typescript = generate_typescript_types(ir)
    zod = generate_zod_schemas(ir)

    assert "address: Address;" in typescript
    assert "aliases: Array<string>;" in typescript
    assert "labels: Record<string, string>;" in typescript
    assert "score: number | null;" in typescript
    assert "active: boolean;" in typescript
    assert "opened_at: string;" in typescript

    assert 'import { z } from "zod";' in zod
    assert 'import type { Address, Incident } from "./types";' in zod
    assert "address: z.lazy(() => AddressSchema)," in zod
    assert "aliases: z.array(z.string()).default([])," in zod
    assert "labels: z.record(z.string(), z.string())," in zod
    assert "score: z.number().nullable().default(null)," in zod
    assert "active: z.boolean().default(true)," in zod
    assert "opened_at: z.string().datetime()," in zod
    assert ".strict()," in zod


def test_enum_generation_is_native_and_validates_in_pydantic() -> None:
    status = EnumIR(semantic_id("type", "Status"), "Status", ("accepted", "follow_up", "failed"))
    result = TypeIR(
        semantic_id("type", "Result"),
        "Result",
        (
            TypeFieldIR("status", parse_type_ref("Status")),
            TypeFieldIR("history", parse_type_ref("list[Status]"), has_default=True, default=[]),
        ),
    )
    ir = CanonicalIR.create(types=(result, status))

    python = generate_pydantic_models(ir)
    typescript = generate_typescript_types(ir)
    zod = generate_zod_schemas(ir)
    namespace: dict[str, Any] = {"__name__": "generated_enum_models"}
    exec(compile(python, "<generated>", "exec"), namespace)

    assert "from typing import Literal" in python
    assert "Status = Literal['accepted', 'follow_up', 'failed']" in python
    assert "export type Status = \"accepted\" | \"follow_up\" | \"failed\";" in typescript
    assert 'StatusSchema: z.ZodType<Status> = z.enum(["accepted", "follow_up", "failed"]);' in zod
    assert namespace["Result"](status="accepted").status == "accepted"
    with pytest.raises(ValueError):
        namespace["Result"](status="unknown")


def test_codegen_rejects_missing_named_types_and_nonportable_identifiers() -> None:
    missing = TypeIR(
        semantic_id("type", "Container"),
        "Container",
        (TypeFieldIR("item", parse_type_ref("Missing")),),
    )
    invalid_field = TypeIR(
        semantic_id("type", "KeywordField"),
        "KeywordField",
        (TypeFieldIR("class", parse_type_ref("string")),),
    )

    with pytest.raises(CodeGenerationError, match="CGEN001.*missing contract type `Missing`"):
        generate_code(CanonicalIR.create(types=(missing,)), targets=("python",))
    with pytest.raises(CodeGenerationError, match="CGEN001.*portable generated-code identifier"):
        generate_code(CanonicalIR.create(types=(invalid_field,)), targets=("python",))


def test_write_and_check_freshness_only_touch_deterministic_generated_paths(tmp_path: Any) -> None:
    generated = generate_code(_portable_ir(), targets=("python", "typescript"))
    output_dir = tmp_path / "generated"

    assert stale_generated_paths(generated, output_dir) == tuple(generated.files)
    with pytest.raises(GeneratedCodeStaleError) as exc:
        write_generated_code(generated, output_dir, check=True)
    assert exc.value.stale_paths == tuple(generated.files)
    assert not output_dir.exists()

    written = write_generated_code(generated, output_dir)

    assert tuple(path.relative_to(output_dir).as_posix() for path in written) == tuple(
        str(path) for path in generated.files
    )
    assert stale_generated_paths(generated, output_dir) == ()
    assert write_generated_code(generated, output_dir, check=True) == ()
    assert write_generated_code(generated, output_dir) == ()

    stale_path = output_dir / PYDANTIC_MODELS_PATH
    stale_path.write_text("stale\n")
    unrelated = output_dir / "keep.txt"
    unrelated.write_text("user-owned\n")

    assert stale_generated_paths(generated, output_dir) == (PYDANTIC_MODELS_PATH,)
    assert write_generated_code(generated, output_dir) == (stale_path,)
    assert unrelated.read_text() == "user-owned\n"


def test_separate_targets_can_share_one_generated_source_root(tmp_path: Any) -> None:
    ir = _portable_ir()
    output_dir = tmp_path / "generated"
    python = generate_code(ir, targets=("python",))
    typescript = generate_code(ir, targets=("typescript",))

    write_generated_code(python, output_dir)
    write_generated_code(typescript, output_dir)

    assert write_generated_code(python, output_dir, check=True) == ()
    assert write_generated_code(typescript, output_dir, check=True) == ()
    assert (output_dir / PYDANTIC_MODELS_PATH).is_file()
    assert (output_dir / TYPESCRIPT_TYPES_PATH).is_file()
    assert (output_dir / ZOD_SCHEMAS_PATH).is_file()


def test_generated_code_paths_are_relative_and_normalized() -> None:
    generated = generate_code(CanonicalIR.create(), targets=("python",))

    assert all(isinstance(path, PurePosixPath) and not path.is_absolute() for path in generated.files)


def _portable_ir(*, reverse: bool = False) -> CanonicalIR:
    address = TypeIR(
        semantic_id("type", "Address"),
        "Address",
        (TypeFieldIR("city", parse_type_ref("string")),),
        description="A portable address.",
    )
    incident = TypeIR(
        semantic_id("type", "Incident"),
        "Incident",
        (
            TypeFieldIR("incident_id", parse_type_ref("string")),
            TypeFieldIR("address", parse_type_ref("Address")),
            TypeFieldIR("aliases", parse_type_ref("list[string]"), has_default=True, default=[]),
            TypeFieldIR("labels", parse_type_ref("map[string,string]")),
            TypeFieldIR("score", parse_type_ref("float?"), has_default=True, default=None),
            TypeFieldIR("active", parse_type_ref("boolean"), has_default=True, default=True),
            TypeFieldIR("opened_at", parse_type_ref("datetime")),
        ),
    )
    types = (incident, address) if reverse else (address, incident)
    return CanonicalIR.create(types=types)
