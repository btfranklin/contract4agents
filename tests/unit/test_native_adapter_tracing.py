from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from contract4agents import compile_project, materialize
from contract4agents.ir import SemanticId
from contract4agents.tracing import TraceAttempt, validate_trace_conformance
from contract4agents.tracing._google_adk import GoogleADKNormalizedTraceRouter
from contract4agents.tracing._strands import StrandsNormalizedTraceRouter

ROOT = Path(__file__).resolve().parents[2]


class _NativeAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.hooks: dict[type[object], Any] = {}

    def add_hook(self, callback: Any, event_type: type[object]) -> None:
        self.hooks[event_type] = callback


class _NativeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _fixture() -> tuple[object, object, object, _NativeAgent, _NativeTool]:
    project = ROOT / "examples" / "incident-command"
    artifacts = compile_project(project)
    result = materialize(project, "openai", "test")
    grant = next(
        grant
        for grant in artifacts.ir.grants.values()
        if grant.availability == "enabled"
        and grant.capability_id.kind == "tool"
        and grant.authorization == "approval_required"
    )
    agent = _NativeAgent("c4a_agent_incident_commander_a1b2c3d4")
    tool = _NativeTool("c4a_tool_status_publish_a1b2c3d4")
    graph = SimpleNamespace(
        agents={grant.agent_id: agent},
        grant_objects={grant.id: tool},
        composition_objects={},
    )
    return artifacts.ir, result.plan, graph, agent, tool


def _install_fake_strands_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, type[object]]:
    event_types = {
        "BeforeInvocationEvent": type("BeforeInvocationEvent", (), {}),
        "AfterInvocationEvent": type("AfterInvocationEvent", (), {}),
        "BeforeModelCallEvent": type("BeforeModelCallEvent", (), {}),
        "AfterModelCallEvent": type("AfterModelCallEvent", (), {}),
        "BeforeToolCallEvent": type("BeforeToolCallEvent", (), {}),
        "AfterToolCallEvent": type("AfterToolCallEvent", (), {}),
    }
    strands = ModuleType("strands")
    hooks = ModuleType("strands.hooks")
    for name, event_type in event_types.items():
        setattr(hooks, name, event_type)
    monkeypatch.setitem(sys.modules, "strands", strands)
    monkeypatch.setitem(sys.modules, "strands.hooks", hooks)
    return event_types


def test_strands_hook_bridge_closes_attempt_and_correlates_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ir, plan, graph, agent, tool = _fixture()
    event_types = _install_fake_strands_hooks(monkeypatch)
    router = StrandsNormalizedTraceRouter()
    bridge = router.attach(graph)
    session = router.open_session(ir, plan, run_id="run-strands")
    attempt = TraceAttempt("invoke:1", "attempt-strands-1", 1)

    with session:
        with session.bind_attempt(attempt, agent=next(iter(graph.agents))):
            agent.hooks[event_types["BeforeInvocationEvent"]](
                SimpleNamespace(
                    agent=agent,
                    invocation_state={"trace_id": "strands-trace-1"},
                )
            )
            agent.hooks[event_types["BeforeModelCallEvent"]](
                SimpleNamespace(agent=agent)
            )
            agent.hooks[event_types["AfterModelCallEvent"]](
                SimpleNamespace(agent=agent, exception=None)
            )
            session.record_approval(
                native_tool=tool,
                approved=True,
                provider_identity="tool-use-1",
            )
            tool_event = SimpleNamespace(
                agent=agent,
                selected_tool=tool,
                tool_use={"toolUseId": "tool-use-1", "name": tool.name},
            )
            agent.hooks[event_types["BeforeToolCallEvent"]](tool_event)
            agent.hooks[event_types["AfterToolCallEvent"]](
                SimpleNamespace(**vars(tool_event), exception=None, result={"ok": True})
            )
            agent.hooks[event_types["AfterInvocationEvent"]](
                SimpleNamespace(
                    agent=agent,
                    exception=None,
                    result=SimpleNamespace(structured_output={"status": "ok"}),
                )
            )

    assert bridge is not None
    snapshot = session.closed_snapshot
    assert snapshot.closure.status == "complete"
    assert snapshot.closure.attempts[0].provider_trace_ids == (
        "strands-trace-1",
    )
    assert [event.event_type for event in snapshot.trace.events] == [
        "agent.started",
        "provider.response.started",
        "provider.response.normalized",
        "approval.completed",
        "tool.started",
        "tool.completed",
        "provider.response.normalized",
        "output.accepted",
        "agent.completed",
    ]
    tool_event = next(
        event for event in snapshot.trace.events if event.event_type == "tool.started"
    )
    grant = ir.grants[tool_event.semantic.grant_id]
    assert tool_event.semantic.agent_id == grant.agent_id
    assert tool_event.semantic.capability_id == grant.capability_id
    validate_trace_conformance(ir, plan, snapshot.trace)


class _BasePlugin:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.mark.asyncio
async def test_google_adk_plugin_is_lazy_and_preserves_grounding_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ir, plan, graph, agent, tool = _fixture()
    module = ModuleType("google.adk.plugins.base_plugin")
    module.BasePlugin = _BasePlugin  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "contract4agents.tracing._google_adk.import_module",
        lambda name: module,
    )
    router = GoogleADKNormalizedTraceRouter().attach(graph)
    plugin = router.plugin()
    session = router.open_session(ir, plan, run_id="run-adk")
    attempt = TraceAttempt("invoke:1", "attempt-adk-1", 1)
    invocation_context = SimpleNamespace(invocation_id="adk-invocation-1")
    callback_context = SimpleNamespace(agent_name=agent.name)
    tool_context = SimpleNamespace(function_call_id="adk-tool-1")

    with session:
        with session.bind_attempt(attempt, agent=next(iter(graph.agents))):
            await plugin.before_run_callback(invocation_context=invocation_context)
            await plugin.before_agent_callback(
                agent=agent,
                callback_context=callback_context,
            )
            await plugin.before_model_callback(
                callback_context=callback_context,
                llm_request=object(),
            )
            await plugin.after_model_callback(
                callback_context=callback_context,
                llm_response=object(),
            )
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"private": "not retained"},
                tool_context=tool_context,
            )
            await plugin.after_tool_callback(
                tool=tool,
                tool_args={"private": "not retained"},
                tool_context=tool_context,
                result={"private": "not retained"},
            )
            await plugin.on_event_callback(
                invocation_context=invocation_context,
                event=SimpleNamespace(
                    id="grounding-1",
                    author=agent.name,
                    grounding_metadata=SimpleNamespace(
                        grounding_chunks=[object(), object()],
                        grounding_supports=[object()],
                        search_entry_point=SimpleNamespace(
                            rendered_content="<div>search suggestions</div>"
                        ),
                    ),
                ),
            )
            await plugin.after_agent_callback(
                agent=agent,
                callback_context=callback_context,
            )
            from contract4agents.materialization._google_adk import (
                _OUTPUT_VALIDATION_OBSERVER,
            )

            observer = _OUTPUT_VALIDATION_OBSERVER.get()
            assert observer is not None
            observer("NestedChild", True)
            observer(next(iter(graph.agents)).parts[0], True)
            await plugin.after_run_callback(invocation_context=invocation_context)

    snapshot = session.closed_snapshot
    assert snapshot.closure.status == "complete"
    grounding = next(
        event
        for event in snapshot.trace.events
        if event.event_type == "provider.grounding_metadata"
    )
    assert grounding.data == {
        "attempt": attempt.to_dict(),
        "grounding_chunk_count": 2,
        "grounding_support_count": 1,
        "rendered_content_present": True,
        "search_entry_point_present": True,
    }
    rendered = json.dumps([event.to_dict() for event in snapshot.trace.events])
    assert "search suggestions" not in rendered
    assert "not retained" not in rendered
    output = next(
        event
        for event in snapshot.trace.events
        if event.event_type == "output.accepted"
    )
    assert output.evidence_refs == ("google-adk:terminal-schema-validation",)
    assert sum(
        event.event_type == "output.accepted"
        for event in snapshot.trace.events
    ) == 1
    validate_trace_conformance(ir, plan, snapshot.trace)


@pytest.mark.asyncio
async def test_google_adk_materializer_validation_failure_seals_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ir, plan, graph, agent, tool = _fixture()
    del tool
    module = ModuleType("google.adk.plugins.base_plugin")
    module.BasePlugin = _BasePlugin  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "contract4agents.tracing._google_adk.import_module",
        lambda name: module,
    )
    router = GoogleADKNormalizedTraceRouter().attach(graph)
    plugin = router.plugin()
    session = router.open_session(ir, plan, run_id="run-adk-invalid")
    attempt = TraceAttempt("invoke:1", "attempt-adk-invalid-1", 1)
    invocation_context = SimpleNamespace(invocation_id="adk-invalid-1")
    callback_context = SimpleNamespace(agent_name=agent.name)

    with session:
        with session.bind_attempt(attempt, agent=next(iter(graph.agents))):
            await plugin.before_run_callback(
                invocation_context=invocation_context
            )
            await plugin.before_agent_callback(
                agent=agent,
                callback_context=callback_context,
            )
            await plugin.before_model_callback(
                callback_context=callback_context,
                llm_request=object(),
            )
            await plugin.after_model_callback(
                callback_context=callback_context,
                llm_response=object(),
            )
            from contract4agents.materialization._google_adk import (
                _OUTPUT_VALIDATION_OBSERVER,
            )

            observer = _OUTPUT_VALIDATION_OBSERVER.get()
            assert observer is not None
            observer(next(iter(graph.agents)).parts[0], False)
            await plugin.on_agent_error_callback(
                agent=agent,
                callback_context=callback_context,
                error=ValueError("invalid output"),
            )
            await plugin.on_run_error_callback(
                invocation_context=invocation_context,
                error=ValueError("invalid output"),
            )

    snapshot = session.closed_snapshot
    assert snapshot.closure.status == "incomplete"
    assert any(
        event.event_type == "output.schema_failed"
        for event in snapshot.trace.events
    )
    assert not any(
        event.event_type == "output.accepted"
        for event in snapshot.trace.events
    )


def test_native_router_rejects_one_name_for_multiple_semantic_ids() -> None:
    ir, plan, graph, agent, tool = _fixture()
    del ir, plan, tool
    other_id = SemanticId.parse("agent:OtherAgent")
    conflicting = SimpleNamespace(
        agents={**graph.agents, other_id: _NativeAgent(agent.name)},
        grant_objects=graph.grant_objects,
        composition_objects={},
    )

    with pytest.raises(ValueError, match="maps to multiple semantic IDs"):
        StrandsNormalizedTraceRouter().register_graph(conflicting)


def test_strands_hook_without_host_attempt_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ir, plan, graph, agent, tool = _fixture()
    del tool
    event_types = _install_fake_strands_hooks(monkeypatch)
    router = StrandsNormalizedTraceRouter()
    router.attach(graph)
    session = router.open_session(ir, plan, run_id="run-unbound")

    with session:
        agent.hooks[event_types["BeforeInvocationEvent"]](
            SimpleNamespace(agent=agent, invocation_state={})
        )
        agent.hooks[event_types["AfterInvocationEvent"]](
            SimpleNamespace(agent=agent, exception=None, result=object())
        )

    assert session.closed_snapshot.closure.status == "incomplete"
    assert session.closed_snapshot.trace.events[0].event_type == (
        "instrumentation.unbound"
    )


def test_strands_missing_structured_output_records_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ir, plan, graph, agent, tool = _fixture()
    del tool
    event_types = _install_fake_strands_hooks(monkeypatch)
    router = StrandsNormalizedTraceRouter()
    router.attach(graph)
    session = router.open_session(ir, plan, run_id="run-invalid-output")
    attempt = TraceAttempt("invoke:1", "attempt-invalid-output-1", 1)

    with session:
        with session.bind_attempt(attempt, agent=next(iter(graph.agents))):
            agent.hooks[event_types["BeforeInvocationEvent"]](
                SimpleNamespace(
                    agent=agent,
                    invocation_state={"trace_id": "strands-invalid-output"},
                )
            )
            agent.hooks[event_types["AfterInvocationEvent"]](
                SimpleNamespace(
                    agent=agent,
                    exception=None,
                    result=SimpleNamespace(structured_output=None),
                )
            )

    snapshot = session.closed_snapshot
    assert snapshot.closure.status == "incomplete"
    assert any(
        event.event_type == "output.schema_failed"
        for event in snapshot.trace.events
    )
    assert not any(
        event.event_type == "output.accepted"
        for event in snapshot.trace.events
    )


def test_strands_nested_delegate_invocations_share_one_host_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ROOT / "examples" / "incident-command"
    ir = compile_project(project).ir
    plan = materialize(project, "openai", "test").plan
    edge = next(iter(ir.composition.values()))
    parent = _NativeAgent("c4a_agent_parent_a1b2c3d4")
    child = _NativeAgent("c4a_agent_child_a1b2c3d4")
    delegate = _NativeTool("c4a_delegate_investigate_a1b2c3d4")
    graph = SimpleNamespace(
        agents={
            edge.source_agent_id: parent,
            edge.target_agent_id: child,
        },
        grant_objects={},
        composition_objects={edge.id: delegate},
    )
    event_types = _install_fake_strands_hooks(monkeypatch)
    router = StrandsNormalizedTraceRouter()
    router.attach(graph)
    session = router.open_session(ir, plan, run_id="run-nested")
    attempt = TraceAttempt("invoke:1", "attempt-nested-1", 1)
    shared_state = {"trace_id": "strands-shared-trace"}

    with session:
        with session.bind_attempt(attempt, agent=edge.source_agent_id):
            parent.hooks[event_types["BeforeInvocationEvent"]](
                SimpleNamespace(agent=parent, invocation_state=shared_state)
            )
            parent.hooks[event_types["BeforeToolCallEvent"]](
                SimpleNamespace(
                    selected_tool=delegate,
                    tool_use={"toolUseId": "delegate-1", "name": delegate.name},
                )
            )
            child.hooks[event_types["BeforeInvocationEvent"]](
                SimpleNamespace(agent=child, invocation_state=shared_state)
            )
            internal_output_tool = _NativeTool("Result")
            internal_output_tool.tool_type = "structured_output"
            internal_event = SimpleNamespace(
                agent=child,
                selected_tool=internal_output_tool,
                tool_use={"toolUseId": "result-1", "name": "Result"},
            )
            child.hooks[event_types["BeforeToolCallEvent"]](internal_event)
            child.hooks[event_types["AfterToolCallEvent"]](
                SimpleNamespace(
                    **vars(internal_event),
                    exception=None,
                    result={"ok": True},
                )
            )
            child.hooks[event_types["AfterInvocationEvent"]](
                SimpleNamespace(
                    agent=child,
                    exception=None,
                    result=SimpleNamespace(structured_output={"child": "ok"}),
                )
            )
            parent.hooks[event_types["AfterToolCallEvent"]](
                SimpleNamespace(
                    selected_tool=delegate,
                    tool_use={"toolUseId": "delegate-1", "name": delegate.name},
                    exception=None,
                    result={"ok": True},
                )
            )
            parent.hooks[event_types["AfterInvocationEvent"]](
                SimpleNamespace(
                    agent=parent,
                    exception=None,
                    result=SimpleNamespace(structured_output={"parent": "ok"}),
                )
            )

    snapshot = session.closed_snapshot
    assert snapshot.closure.status == "complete"
    assert set(snapshot.closure.attempts[0].provider_trace_ids) == {
        "strands-shared-trace",
        "strands-shared-trace:nested:2",
    }
    assert any(
        event.event_type == "composition.started"
        and event.semantic.composition_id == edge.id
        for event in snapshot.trace.events
    )
    assert any(
        event.event_type == "agent.started"
        and event.semantic.agent_id == edge.target_agent_id
        for event in snapshot.trace.events
    )
    assert not any(
        event.event_type == "capability.undeclared"
        for event in snapshot.trace.events
    )


def test_strands_interrupt_then_resume_is_not_an_output_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ir, plan, graph, agent, tool = _fixture()
    event_types = _install_fake_strands_hooks(monkeypatch)
    router = StrandsNormalizedTraceRouter()
    router.attach(graph)
    session = router.open_session(ir, plan, run_id="run-resume")
    attempt = TraceAttempt("invoke:1", "attempt-resume-1", 1)

    with session:
        with session.bind_attempt(attempt, agent=next(iter(graph.agents))):
            for position, result in enumerate(
                (
                    SimpleNamespace(
                        stop_reason="interrupt",
                        structured_output=None,
                    ),
                    SimpleNamespace(
                        stop_reason="end_turn",
                        structured_output={"status": "ok"},
                    ),
                )
            ):
                agent.hooks[event_types["BeforeInvocationEvent"]](
                    SimpleNamespace(agent=agent, invocation_state={})
                )
                agent.hooks[event_types["AfterInvocationEvent"]](
                    SimpleNamespace(agent=agent, exception=None, result=result)
                )
                if position == 0:
                    session.record_approval_requested(native_tool=tool)
                    session.record_approval(native_tool=tool, approved=True)

    snapshot = session.closed_snapshot
    assert snapshot.closure.status == "complete"
    assert any(
        event.event_type == "agent.interrupted"
        for event in snapshot.trace.events
    )
    assert not any(
        event.event_type == "output.schema_failed"
        for event in snapshot.trace.events
    )
    assert {
        "approval.requested",
        "approval.completed",
    }.issubset({event.event_type for event in snapshot.trace.events})
    assert sum(
        event.event_type == "output.accepted"
        for event in snapshot.trace.events
    ) == 1


def test_google_adk_plugin_missing_extra_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(name: str) -> ModuleType:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(
        "contract4agents.tracing._google_adk.import_module",
        missing,
    )

    with pytest.raises(RuntimeError, match=r"contract4agents\[google-adk\]"):
        GoogleADKNormalizedTraceRouter().plugin()


def test_strands_attach_uses_installed_public_hook_types() -> None:
    hooks = pytest.importorskip("strands.hooks")
    ir, plan, graph, agent, tool = _fixture()
    del ir, plan, tool

    StrandsNormalizedTraceRouter().attach(graph)

    assert hooks.BeforeInvocationEvent in agent.hooks
    assert hooks.AfterInvocationEvent in agent.hooks
    assert hooks.BeforeModelCallEvent in agent.hooks
    assert hooks.AfterModelCallEvent in agent.hooks
    assert hooks.BeforeToolCallEvent in agent.hooks
    assert hooks.AfterToolCallEvent in agent.hooks


def test_google_adk_plugin_subclasses_installed_base_plugin() -> None:
    plugin_module = pytest.importorskip("google.adk.plugins.base_plugin")

    plugin = GoogleADKNormalizedTraceRouter().plugin()

    assert isinstance(plugin, plugin_module.BasePlugin)
