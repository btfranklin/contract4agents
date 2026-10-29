from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import cast

import pytest
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.events import Event
from google.adk.models import BaseLlm
from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from contract4agents import MaterializedSystem, compile_project
from contract4agents.compiler import artifact_digests
from contract4agents.ir import FrozenMap, SemanticId, semantic_id
from contract4agents.materialization import MaterializationError, RecordingMaterializationTraceSink
from contract4agents.materialization._context import ContextRuntime
from contract4agents.materialization._google_adk import (
    ADKSDK,
    GoogleADKMaterializationProvider,
    GoogleADKOutputValidationError,
)
from contract4agents.materialization._types import build_agent_input_types, build_pydantic_types
from contract4agents.planning import plan_materialization
from contract4agents.target_bindings import BindingEntry, TargetBinding, TargetBindings, TargetProfile
from contract4agents.tracing import (
    GoogleADKNormalizedTraceRouter,
    TraceAttempt,
    validate_trace_closure,
    validate_trace_conformance,
)


def _system(
    tmp_path: Path, model: BaseLlm, *, sdk: ADKSDK | None = None, enum_return: bool = False,
) -> tuple[MaterializedSystem, list[str]]:
    (tmp_path / "reply.types.contract").write_text(
        'type Reply:\n    summary: string\n\nenum Status:\n    "accepted"\n    "failed"\n'
    )
    (tmp_path / "lookup.tool.contract").write_text(
        'tool records.lookup(query: string) -> Reply:\n'
        '    description = "Look up a record."\n'
        '    side_effect = false\n'
    )
    (tmp_path / "analyst.agent.contract").write_text(
        f'agent Analyst(question: string) -> {"Status" if enum_return else "Reply"}:\n'
        '    use records.lookup:\n'
        '        availability = enabled\n'
        '        authorization = preapproved\n'
        '        execution = host\n'
        '    goal = "Summarize the matching record."\n'
    )
    artifacts = compile_project(tmp_path)
    target = TargetBinding(
        adapter="google_adk",
        tools={"records.lookup": BindingEntry({"python": "app:lookup"})},
        profiles={"test": TargetProfile(default_model=model.model, options={"model_factory": "app:model"})},
    )
    provider = GoogleADKMaterializationProvider(sdk or ADKSDK())
    plan = plan_materialization(
        artifacts.ir,
        TargetBindings(path=tmp_path / "contract4agents.targets.toml", targets={"google_adk": target}),
        target="google_adk",
        profile="test",
        capabilities=provider.planner_capabilities(None),
        artifact_digests=artifact_digests(artifacts),
    )
    calls: list[str] = []

    def lookup(query: str) -> dict[str, str]:
        calls.append(query)
        return {"summary": "The record is ready."}

    def factory(*, model: str, options: Mapping[str, object]) -> BaseLlm:
        del model, options
        return supplied_model

    supplied_model = model
    implementations: FrozenMap[SemanticId, object] = FrozenMap(
        {semantic_id("agent", "Analyst"): factory, semantic_id("tool", "records.lookup"): lookup}
    )
    output_types = build_pydantic_types(artifacts.ir)
    graph = provider.build_graph(
        ir=artifacts.ir,
        artifacts=artifacts,
        target=target,
        plan=plan,
        implementations=implementations,
        input_types=build_agent_input_types(artifacts.ir, output_types),
        output_types=output_types,
        context_runtime=ContextRuntime(artifacts.ir, plan, implementations, output_types),
        environment=None,
        materialization_trace_sink=RecordingMaterializationTraceSink(),
    )
    return MaterializedSystem(artifacts=artifacts, plan=plan, graph=graph), calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("native_schema", "correct_after_invalid", "invalid_terminal"),
    [(True, False, False), (False, False, False), (False, True, False), (True, False, True), (False, True, True)],
)
async def test_adk_selects_structured_output_from_model_capabilities_and_preserves_trace(
    tmp_path: Path,
    native_schema: bool,
    correct_after_invalid: bool,
    invalid_terminal: bool,
) -> None:
    requests: list[LlmRequest] = []

    class ScriptedModel(BaseLlm):  # type: ignore[explicit-any]  # Pydantic inherits dynamic model options.
        @property
        def capabilities(self) -> LlmCapabilities:
            return LlmCapabilities(output_schema_and_tools=native_schema)

        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False,
        ) -> AsyncGenerator[LlmResponse, None]:
            del stream
            requests.append(llm_request)
            assert "set_model_response" in llm_request.tools_dict or native_schema
            if native_schema:
                assert llm_request.config.response_schema is not None
                assert "set_model_response" not in llm_request.tools_dict
            else:
                assert llm_request.config.response_schema is None
            if len(requests) == 1:
                lookup_name = next(name for name in llm_request.tools_dict if name != "set_model_response")
                parts = [types.Part.from_function_call(name=lookup_name, args={"query": "record-1"})]
            elif native_schema or invalid_terminal and len(requests) == 3:
                payload = {"summary": 7 if invalid_terminal else "The record is ready."}
                parts = [types.Part.from_text(text=json.dumps(payload))]
            else:
                summary: object = 7 if correct_after_invalid and len(requests) == 2 else "The record is ready."
                parts = [
                    types.Part.from_text(text="The result is ready."),
                    types.Part.from_function_call(name="set_model_response", args={"summary": summary}),
                ]
            assert len(requests) <= 3
            yield LlmResponse(content=types.Content(role="model", parts=parts))

    # The name deliberately differs from Gemini names: the SDK must use the capability.
    system, calls = _system(tmp_path, ScriptedModel(model="capability-test-model"))
    agent = cast(LlmAgent, system.agents["Analyst"])
    assert agent.output_schema is system.output_type_for_agent(agent)
    assert all(item.matches for item in system.validation.schema_conformance)
    assert next(
        item for item in system.validation.configuration_conformance if item.property_path == "agent.output_mode"
    ).status == "passed"
    router = GoogleADKNormalizedTraceRouter().attach(system)
    services = InMemorySessionService()
    await services.create_session(app_name="output_test", user_id="user", session_id="session")
    runner = Runner(
        app=App(name="output_test", root_agent=agent, plugins=[cast(BasePlugin, router.plugin())]),
        session_service=services,
    )
    session = router.open_session(system, run_id="run-1")
    attempt = TraceAttempt("invocation-1", "attempt-1", 1)
    events: list[Event] = []
    try:
        with pytest.raises(GoogleADKOutputValidationError) if invalid_terminal else nullcontext():
            with session:
                with session.bind_attempt(attempt, agent=agent):
                    async for event in runner.run_async(
                        user_id="user",
                        session_id="session",
                        new_message=types.Content(
                            role="user",
                            parts=[types.Part.from_text(
                                text=system.serialize_input_for_agent(agent, {"question": "Find it."}),
                            )],
                        ),
                    ):
                        events.append(event)
                session.record_terminal_attempt(attempt=attempt, outcome="succeeded")
    finally:
        await runner.close()

    assert calls == ["record-1"]
    assert len(requests) == (3 if correct_after_invalid and not native_schema else 2)
    snapshot = session.closed_snapshot
    event_types = [event.event_type for event in snapshot.trace.events]
    if invalid_terminal:
        assert "output.schema_failed" in event_types
        assert "output.accepted" not in event_types
        assert snapshot.closure.status == "incomplete"
        return
    final = [event for event in events if event.is_final_response()][-1]
    assert final.content is not None and final.content.parts is not None
    output = system.validate_output_for_agent(agent, json.loads(final.content.parts[0].text or ""))
    assert output.summary == "The record is ready."  # type: ignore[attr-defined]  # Generated contract field.
    assert snapshot.closure.status == "complete"
    validate_trace_conformance(system, snapshot.trace)
    validate_trace_closure(snapshot.trace, snapshot.closure)
    assert event_types.count("output.accepted") == 1
    assert event_types.count("tool.started") == event_types.count("tool.completed") == 1
    assert "capability.undeclared" not in event_types
    assert "output.schema_failed" not in event_types


def test_adk_materialization_rejects_a_dropped_output_schema(tmp_path: Path) -> None:
    class DroppedSchemaSDK(ADKSDK):
        def attach(self, agent: object, *, tools: tuple[object, ...]) -> None:
            super().attach(agent, tools=tools)
            cast(LlmAgent, agent).output_schema = None

    class UnusedModel(BaseLlm):  # type: ignore[explicit-any]  # Pydantic inherits dynamic model options.
        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False,
        ) -> AsyncGenerator[LlmResponse, None]:
            raise AssertionError("Materialization must not call the model")
            yield LlmResponse()  # pragma: no cover

    with pytest.raises(MaterializationError, match="MAT425"):
        _system(tmp_path, UnusedModel(model="unused-model"), sdk=DroppedSchemaSDK())


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["accepted", "unknown"])
async def test_adk_enum_returns_preserve_the_scalar_validation_path(tmp_path: Path, value: str) -> None:
    class EnumModel(BaseLlm):  # type: ignore[explicit-any]  # Pydantic inherits dynamic model options.
        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False,
        ) -> AsyncGenerator[LlmResponse, None]:
            del stream
            assert llm_request.config.response_schema is None
            assert "set_model_response" not in llm_request.tools_dict
            assert '"enum":["accepted","failed"]' in str(llm_request.config.system_instruction)
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(value))]))

    system, _ = _system(tmp_path, EnumModel(model="enum-test-model"), enum_return=True)
    agent = cast(LlmAgent, system.agents["Analyst"])
    assert agent.output_schema is None
    output_control = system.plan.controls[semantic_id("control", "Analyst", "output_conformance")]
    assert output_control.outcome == "emulated"
    assert output_control.mechanism == "google_adk.terminal_output_schema_validation"
    assert all(item.matches for item in system.validation.schema_conformance)
    services = InMemorySessionService()
    await services.create_session(app_name="enum_test", user_id="user", session_id="session")
    runner = Runner(app_name="enum_test", agent=agent, session_service=services)
    try:
        with pytest.raises(GoogleADKOutputValidationError) if value == "unknown" else nullcontext():
            events = [event async for event in runner.run_async(
                user_id="user", session_id="session",
                new_message=types.Content(role="user", parts=[types.Part.from_text(
                    text=system.serialize_input_for_agent(agent, {"question": "Choose a status."}),
                )]),
            )]
            final = [event for event in events if event.is_final_response()][-1]
            assert final.content is not None and final.content.parts is not None
            assert system.validate_output_for_agent(agent, json.loads(final.content.parts[0].text or "")) == "accepted"
    finally:
        await runner.close()
