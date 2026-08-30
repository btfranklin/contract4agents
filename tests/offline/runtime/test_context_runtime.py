from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from contract4agents import materialize
from contract4agents.ir import (
    FrozenMap,
    semantic_id,
)
from contract4agents.materialization import (
    ContextResolutionError,
    ContextRuntime,
    OpenAIMaterializationProvider,
)
from contract4agents.tracing import NoOpNormalizedTraceSink, RecordingNormalizedTraceSink
from tests.support.openai import FakeOpenAISDK, write_project


@pytest.mark.asyncio
async def test_materialized_context_runtime_maps_validates_caches_renders_and_traces(tmp_path: Path) -> None:
    write_project(tmp_path)
    runtime_sink = RecordingNormalizedTraceSink()
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
        normalized_trace_sink=runtime_sink,
    )

    first = await result.context.resolve_agent(
        "Child",
        {"request": {"value": "needle"}},
        run_id="run-1",
        thread_id="thread-1",
    )
    second = await result.context.resolve_agent(
        "Child",
        {"request": {"value": "needle"}},
        run_id="run-1",
        thread_id="thread-1",
    )

    assert cast(Any, first["current"].value).value == "needle"
    assert first["current"].rendered == "- **value:** needle"
    assert first["current"].from_cache is False
    assert second["current"].from_cache is True
    assert cast(Any, first["metadata"].value).value == "context"
    assert second["metadata"].from_cache is True
    assert [event.event_type for event in runtime_sink.events] == [
        "datasource.resolved",
        "context.resolved",
        "datasource.resolved",
        "context.resolved",
    ]
    assert runtime_sink.events[0].semantic.context_id == semantic_id("context", "Child", "current")
    assert runtime_sink.events[0].semantic.capability_id == semantic_id("datasource", "records.current")
    assert runtime_sink.events[1].data["sensitivity"] == "internal"
    assert runtime_sink.events[0].context.plan_digest == result.plan.plan_digest
    assert all("value" not in event.data for event in runtime_sink.events)
    NoOpNormalizedTraceSink().emit(runtime_sink.events[0])

    result.context.complete_run("run-1")
    third = await result.context.resolve_agent(
        "Child",
        {"request": {"value": "needle"}},
        run_id="run-1",
    )
    assert third["current"].from_cache is False


@pytest.mark.asyncio
async def test_materialized_context_runtime_rejects_invalid_invocation_shape(tmp_path: Path) -> None:
    write_project(tmp_path)
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    with pytest.raises(ContextResolutionError, match="input validation failed"):
        await result.context.resolve_agent("Child", {"request": {}}, run_id="run-1")

    with pytest.raises(KeyError):
        await result.context.resolve_agent("Missing", {}, run_id="run-1")
    with pytest.raises(ValueError, match="run_id"):
        await result.context.resolve_agent("Child", {"request": {"value": "ok"}}, run_id="")


@pytest.mark.asyncio
async def test_context_runtime_enforces_thread_cache_and_records_provider_failures(tmp_path: Path) -> None:
    write_project(tmp_path, datasource_cache="thread", async_current=True)
    sink = RecordingNormalizedTraceSink()
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
        normalized_trace_sink=sink,
    )

    first = await result.context.resolve_agent(
        "Child", {"request": {"value": "ok"}}, run_id="run-1", thread_id="thread-1"
    )
    second = await result.context.resolve_agent(
        "Child", {"request": {"value": "ok"}}, run_id="run-2", thread_id="thread-1"
    )
    assert first["current"].from_cache is False
    assert second["current"].from_cache is True
    result.context.complete_thread("thread-1")
    third = await result.context.resolve_agent(
        "Child", {"request": {"value": "ok"}}, run_id="run-3", thread_id="thread-1"
    )
    assert third["current"].from_cache is False

    broken_root = tmp_path / "broken"
    broken_root.mkdir()
    write_project(broken_root, invalid_current=True)
    broken_sink = RecordingNormalizedTraceSink()
    broken = materialize(
        broken_root,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
        normalized_trace_sink=broken_sink,
    )
    with pytest.raises(ContextResolutionError, match="output validation failed"):
        await broken.context.resolve_agent("Child", {"request": {"value": "bad"}}, run_id="run-broken")
    with pytest.raises(ContextResolutionError, match="output validation failed"):
        await broken.context.resolve_agent("Child", {"request": {"value": "bad"}}, run_id="run-broken")
    failures = [event for event in broken_sink.events if event.event_type == "datasource.failed"]
    assert len(failures) == 2
    assert all(event.data == {"error_type": "ValidationError"} for event in failures)


@pytest.mark.asyncio
async def test_context_runtime_uses_single_flight_and_requires_inactive_completion(
    tmp_path: Path,
) -> None:
    write_project(tmp_path)
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def current(query: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"value": query}

    datasource_id = semantic_id("datasource", "records.current")
    implementations = FrozenMap(
        (
            identifier,
            current if identifier == datasource_id else implementation,
        )
        for identifier, implementation in result.context.implementations.items()
    )
    runtime = ContextRuntime(
        result.context.ir,
        result.context.plan,
        implementations,
        result.context.output_types,
    )
    first_task = asyncio.create_task(
        runtime.resolve_agent(
            "Child",
            {"request": {"value": "same"}},
            run_id="run-1",
            thread_id="thread-1",
        )
    )
    await started.wait()
    second_task = asyncio.create_task(
        runtime.resolve_agent(
            "Child",
            {"request": {"value": "same"}},
            run_id="run-1",
            thread_id="thread-1",
        )
    )
    await asyncio.sleep(0)

    assert calls == 1
    with pytest.raises(RuntimeError, match="resolution is active"):
        runtime.complete_run("run-1")

    release.set()
    first, second = await asyncio.gather(first_task, second_task)
    assert first["current"].from_cache is False
    assert second["current"].from_cache is True
    assert calls == 1

    runtime.complete_run("run-1")
    third = await runtime.resolve_agent(
        "Child",
        {"request": {"value": "same"}},
        run_id="run-1",
        thread_id="thread-1",
    )
    assert third["current"].from_cache is False
    assert calls == 2


@pytest.mark.asyncio
async def test_context_runtime_waiter_cancellation_does_not_cancel_shared_resolution(
    tmp_path: Path,
) -> None:
    write_project(tmp_path)
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def current(query: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"value": query}

    datasource_id = semantic_id("datasource", "records.current")
    implementations = FrozenMap(
        (
            identifier,
            current if identifier == datasource_id else implementation,
        )
        for identifier, implementation in result.context.implementations.items()
    )
    runtime = ContextRuntime(
        result.context.ir,
        result.context.plan,
        implementations,
        result.context.output_types,
    )
    first = asyncio.create_task(
        runtime.resolve_agent(
            "Child",
            {"request": {"value": "shared"}},
            run_id="run-1",
        )
    )
    await started.wait()
    waiter = asyncio.create_task(
        runtime.resolve_agent(
            "Child",
            {"request": {"value": "shared"}},
            run_id="run-1",
        )
    )
    await asyncio.sleep(0)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert calls == 1

    release.set()
    resolved = await first
    cached = await runtime.resolve_agent(
        "Child",
        {"request": {"value": "shared"}},
        run_id="run-1",
    )

    assert cast(Any, resolved["current"].value).value == "shared"
    assert cached["current"].from_cache is True
    assert calls == 1


@pytest.mark.asyncio
async def test_context_runtime_retries_after_provider_cancellation(tmp_path: Path) -> None:
    write_project(tmp_path)
    result = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )
    calls = 0

    async def current(query: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError
        return {"value": query}

    datasource_id = semantic_id("datasource", "records.current")
    implementations = FrozenMap(
        (
            identifier,
            current if identifier == datasource_id else implementation,
        )
        for identifier, implementation in result.context.implementations.items()
    )
    runtime = ContextRuntime(
        result.context.ir,
        result.context.plan,
        implementations,
        result.context.output_types,
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime.resolve_agent("Child", {"request": {"value": "retry"}}, run_id="run-1")
    resolved = await runtime.resolve_agent("Child", {"request": {"value": "retry"}}, run_id="run-1")

    assert cast(Any, resolved["current"].value).value == "retry"
    assert calls == 2
