from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import pytest
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    RootModel,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
    ValidationError,
    create_model,
)

from contract4agents._portable_validation import parse_portable_datetime
from contract4agents.ir import FrozenMap, parse_type_ref
from contract4agents.materialization._host_callables import HostCallableBoundary
from contract4agents.materialization._types import type_adapter_for

PortableDatetime = Annotated[datetime, BeforeValidator(parse_portable_datetime)]


class ApplicationItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: str


class ContractItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: StrictStr


class ContractEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ContractItem]


class ContractItems(RootModel[list[ContractItem]]):
    model_config = ConfigDict(strict=True)


ARGUMENTS_TYPE = create_model(
    "HostCallableArguments",
    __config__=ConfigDict(extra="forbid", strict=True),
    query=(StrictStr, ...),
    note=(StrictStr | None, None),
    limit=(StrictInt, 10),
)
DATETIME_ARGUMENT_TYPE = create_model(
    "HostCallableDatetimeArguments",
    __config__=ConfigDict(extra="forbid", strict=True),
    when=(PortableDatetime, ...),
)


def _boundary(
    implementation: Any,
    output: Any = StrictStr,
    *,
    input_type: type[object] | None = ARGUMENTS_TYPE,
) -> HostCallableBoundary:
    return HostCallableBoundary.create(
        "records.lookup",
        implementation,
        input_type,
        TypeAdapter(output),
    )


def test_validate_arguments_applies_optional_and_default_values_in_python_mode() -> None:
    boundary = _boundary(lambda **_arguments: "ok")

    arguments = boundary.validate_arguments({"query": "needle"})

    assert arguments == {"query": "needle", "note": None, "limit": 10}


@pytest.mark.parametrize(
    ("raw", "error_type"),
    (
        ([], ValueError),
        ({}, ValidationError),
        ({"query": "needle", "unknown": True}, ValidationError),
        ({"query": 1}, ValidationError),
    ),
    ids=("not-an-object", "missing", "extra", "wrong-type"),
)
def test_validate_arguments_rejects_invalid_input(
    raw: object,
    error_type: type[Exception],
) -> None:
    boundary = _boundary(lambda **_arguments: "ok")

    with pytest.raises(error_type):
        boundary.validate_arguments(raw)


def test_parameterless_boundary_accepts_only_an_empty_object() -> None:
    boundary = _boundary(lambda: "ok", input_type=None)

    assert boundary.validate_arguments({}) == {}
    with pytest.raises(ValueError, match="does not accept arguments"):
        boundary.validate_arguments({"query": "needle"})


@pytest.mark.asyncio
async def test_invoke_supports_keyword_only_host_parameters() -> None:
    def implementation(*, query: str, note: str | None, limit: int) -> str:
        return f"{query}:{note}:{limit}"

    result = await _boundary(implementation).invoke({"query": "needle"})

    assert result.validated_value == "needle:None:10"
    assert result.json_value == "needle:None:10"


@pytest.mark.parametrize("callable_object", (False, True), ids=("function", "callable-object"))
@pytest.mark.asyncio
async def test_synchronous_hosts_run_on_a_worker_thread(callable_object: bool) -> None:
    event_loop_thread = threading.get_ident()

    def run(**_arguments: object) -> str:
        return str(threading.get_ident())

    if callable_object:

        class SyncCallable:
            def __call__(self, **arguments: object) -> str:
                return run(**arguments)

        implementation: object = SyncCallable()
    else:
        implementation = run

    result = await _boundary(implementation).invoke({"query": "needle"})

    assert result.validated_value != str(event_loop_thread)


@pytest.mark.parametrize("callable_object", (False, True), ids=("function", "callable-object"))
@pytest.mark.asyncio
async def test_asynchronous_hosts_run_on_the_event_loop_thread(callable_object: bool) -> None:
    event_loop_thread = threading.get_ident()

    async def run(**_arguments: object) -> str:
        return str(threading.get_ident())

    if callable_object:

        class AsyncCallable:
            async def __call__(self, **arguments: object) -> str:
                return await run(**arguments)

        implementation: object = AsyncCallable()
    else:
        implementation = run

    result = await _boundary(implementation).invoke({"query": "needle"})

    assert result.validated_value == str(event_loop_thread)


@pytest.mark.asyncio
async def test_sync_wrapper_runs_off_loop_and_its_awaitable_runs_on_loop() -> None:
    event_loop_thread = threading.get_ident()
    observed: list[int] = []

    async def finish() -> str:
        observed.append(threading.get_ident())
        return "ok"

    def implementation(**_arguments: object) -> object:
        observed.append(threading.get_ident())
        return finish()

    result = await _boundary(implementation).invoke({"query": "needle"})

    assert result.validated_value == "ok"
    assert observed[0] != event_loop_thread
    assert observed[1] == event_loop_thread


@pytest.mark.asyncio
async def test_portable_datetime_argument_reaches_host_as_aware_datetime() -> None:
    received: list[object] = []

    def implementation(when: datetime) -> str:
        received.append(when)
        return "ok"

    boundary = _boundary(implementation, input_type=DATETIME_ARGUMENT_TYPE)

    await boundary.invoke({"when": "2026-01-01T00:00:00Z"})

    assert received == [datetime(2026, 1, 1, tzinfo=UTC)]


@pytest.mark.parametrize(
    ("value", "output_type", "expected_python", "expected_json"),
    (
        ({"key": 1}, dict[StrictStr, StrictInt], {"key": 1}, {"key": 1}),
        ([1, 2], list[StrictInt], [1, 2], [1, 2]),
        (7, StrictInt, 7, 7),
        (None, StrictStr | None, None, None),
        (
            datetime(2026, 1, 1, tzinfo=UTC),
            PortableDatetime,
            datetime(2026, 1, 1, tzinfo=UTC),
            "2026-01-01T00:00:00Z",
        ),
    ),
    ids=("mapping", "list", "primitive", "nullable", "datetime"),
)
@pytest.mark.asyncio
async def test_invoke_returns_validated_python_and_json_transport_values(
    value: object,
    output_type: object,
    expected_python: object,
    expected_json: object,
) -> None:
    boundary = _boundary(lambda: value, output_type, input_type=None)

    result = await boundary.invoke({})

    assert result.validated_value == expected_python
    assert result.json_value == expected_json


@pytest.mark.parametrize(
    ("value", "output_type", "expected_json"),
    (
        (
            ApplicationItem(label="needle"),
            ContractItem,
            {"label": "needle"},
        ),
        (
            {"items": [ApplicationItem(label="needle")]},
            ContractEnvelope,
            {"items": [{"label": "needle"}]},
        ),
        (
            RootModel[list[ApplicationItem]]([ApplicationItem(label="needle")]),
            ContractItems,
            [{"label": "needle"}],
        ),
    ),
    ids=("application-model", "nested-model", "root-model"),
)
@pytest.mark.asyncio
async def test_invoke_normalizes_application_pydantic_models(
    value: object,
    output_type: object,
    expected_json: object,
) -> None:
    result = await _boundary(lambda: value, output_type, input_type=None).invoke({})

    assert result.json_value == expected_json


@dataclass
class DataclassItem:
    label: str


class ArbitraryItem:
    def __init__(self, label: str) -> None:
        self.label = label


@pytest.mark.parametrize(
    ("value", "output_type"),
    (
        ({}, ContractItem),
        ({"label": "needle", "extra": True}, ContractItem),
        ({"label": 1}, ContractItem),
        (float("inf"), StrictFloat),
        (DataclassItem(label="needle"), ContractItem),
        ((1, 2), list[StrictInt]),
        (ArbitraryItem(label="needle"), ContractItem),
    ),
    ids=(
        "missing-field",
        "extra-field",
        "scalar-coercion",
        "non-finite-float",
        "dataclass",
        "tuple-for-list",
        "arbitrary-object",
    ),
)
@pytest.mark.asyncio
async def test_invoke_keeps_strict_output_rejections(
    value: object,
    output_type: object,
) -> None:
    adapter = (
        TypeAdapter(output_type)
        if isinstance(output_type, type) and issubclass(output_type, BaseModel)
        else TypeAdapter(output_type, config=ConfigDict(strict=True, allow_inf_nan=False))
    )
    boundary = HostCallableBoundary.create(
        "records.lookup",
        lambda: value,
        None,
        adapter,
    )

    with pytest.raises(ValidationError):
        await boundary.invoke({})


@pytest.mark.asyncio
async def test_generated_list_output_adapter_rejects_a_tuple() -> None:
    boundary = HostCallableBoundary.create(
        "records.lookup",
        lambda: (1, 2),
        None,
        type_adapter_for(parse_type_ref("list[integer]"), FrozenMap()),
    )

    with pytest.raises(ValidationError):
        await boundary.invoke({})


@pytest.mark.asyncio
async def test_application_exception_propagates_without_reclassification() -> None:
    error = RuntimeError("host failed")

    def implementation() -> str:
        raise error

    with pytest.raises(RuntimeError) as caught:
        await _boundary(implementation, input_type=None).invoke({})

    assert caught.value is error


@pytest.mark.asyncio
async def test_application_cancellation_propagates() -> None:
    async def implementation() -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _boundary(implementation, input_type=None).invoke({})
