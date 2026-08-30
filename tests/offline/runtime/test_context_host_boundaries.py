from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, cast

import pytest

from contract4agents import materialize
from contract4agents.materialization import OpenAIMaterializationProvider
from tests.support.openai import FakeOpenAISDK, write_project


@pytest.mark.parametrize("asynchronous", (False, True), ids=("sync", "async"))
@pytest.mark.parametrize("boundary", ("datasource", "external"))
@pytest.mark.asyncio
async def test_context_runtime_accepts_application_pydantic_results(
    tmp_path: Path,
    boundary: str,
    asynchronous: bool,
) -> None:
    write_project(tmp_path)
    current_async = "async " if asynchronous and boundary == "datasource" else ""
    context_async = "async " if asynchronous and boundary == "external" else ""
    current_result = "ApplicationResult(value=query)" if boundary == "datasource" else '{"value": query}'
    context_result = 'ApplicationResult(value="context")' if boundary == "external" else '{"value": "context"}'
    (tmp_path / "app_impl.py").write_text(
        f"""\
from pydantic import BaseModel, ConfigDict

class ApplicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str

def lookup(query: str):
    return {{"value": query}}

{current_async}def current(query: str):
    return {current_result}

{context_async}def context():
    return {context_result}
"""
    )
    system = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    resolved = await system.context.resolve_agent(
        "Child",
        {"request": {"value": "needle"}},
        run_id="run-1",
    )

    assert cast(Any, resolved["current"].value).value == "needle"
    assert cast(Any, resolved["metadata"].value).value == "context"


@pytest.mark.parametrize("callable_shape", ("function", "callable"))
@pytest.mark.parametrize("boundary", ("datasource", "external"))
@pytest.mark.asyncio
async def test_context_runtime_runs_synchronous_providers_off_the_event_loop(
    tmp_path: Path,
    boundary: str,
    callable_shape: str,
) -> None:
    write_project(tmp_path)
    selected_name = "current" if boundary == "datasource" else "context"
    parameter = "query: str" if boundary == "datasource" else ""
    if callable_shape == "function":
        selected_definition = f"""\
def {selected_name}({parameter}):
    return {{"value": str(threading.get_ident())}}
"""
    else:
        selected_definition = f"""\
class SelectedProvider:
    def __call__(self, {parameter}):
        return {{"value": str(threading.get_ident())}}

{selected_name} = SelectedProvider()
"""
    current_definition = (
        selected_definition if boundary == "datasource" else 'def current(query: str):\n    return {"value": query}\n'
    )
    context_definition = (
        selected_definition if boundary == "external" else 'def context():\n    return {"value": "context"}\n'
    )
    (tmp_path / "app_impl.py").write_text(
        'import threading\n\ndef lookup(query: str):\n    return {"value": query}\n\n'
        + current_definition
        + "\n"
        + context_definition
    )
    event_loop_thread = str(threading.get_ident())
    system = materialize(
        tmp_path,
        "openai",
        "test",
        provider=OpenAIMaterializationProvider(FakeOpenAISDK()),
    )

    resolved = await system.context.resolve_agent(
        "Child",
        {"request": {"value": "needle"}},
        run_id="run-1",
    )

    selected_context = "current" if boundary == "datasource" else "metadata"
    assert cast(Any, resolved[selected_context].value).value != event_loop_thread
