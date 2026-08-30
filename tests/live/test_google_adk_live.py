from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from contract4agents import materialize
from contract4agents.tracing import (
    GoogleADKNormalizedTraceRouter,
    TraceAttempt,
    validate_trace_closure,
    validate_trace_conformance,
)
from examples.market_research_brief_imports.seed import seed_market_data

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "examples" / "market-research-brief"


@pytest.mark.integration
@pytest.mark.live
@pytest.mark.asyncio
async def test_google_adk_search_preserves_grounding_display_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("CONTRACT4AGENTS_RUN_GOOGLE_ADK_LIVE") != "1":
        pytest.skip("set CONTRACT4AGENTS_RUN_GOOGLE_ADK_LIVE=1 to run the live ADK test")
    if not os.environ.get("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY is not configured")

    from google.adk.apps import App
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    database = seed_market_data(tmp_path / "market.sqlite")
    monkeypatch.setenv("CONTRACT4AGENTS_MARKET_RESEARCH_DB", str(database))
    system = materialize(PROJECT, target="google_adk", profile="production")
    router = GoogleADKNormalizedTraceRouter().attach(system.graph)
    app_name = "contract4agents_google_adk_live"
    user_id = "contract4agents"
    session_id = "google-adk-live-search"
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    runner = Runner(
        app=App(
            name=app_name,
            root_agent=system.agents["CurrentTruthScout"],
            plugins=[router.plugin()],
        ),
        session_service=session_service,
    )
    trace_session = router.open_session(
        system.context.ir,
        system.plan,
        run_id=session_id,
    )
    attempt = TraceAttempt(
        f"{session_id}:1",
        f"{session_id}:attempt:1",
        1,
    )
    events: list[Any] = []
    try:
        with trace_session:
            with trace_session.bind_attempt(
                attempt,
                agent="CurrentTruthScout",
            ):
                async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                text=(
                                    "Use web.search for current evidence about "
                                    "auditable AI summaries in field operations. "
                                    "Return the required structured evidence."
                                )
                            )
                        ],
                    ),
                ):
                    events.append(event)
    finally:
        await runner.close()

    grounding = [event.grounding_metadata for event in events if getattr(event, "grounding_metadata", None) is not None]
    assert grounding
    assert any(metadata.web_search_queries for metadata in grounding)
    assert any(
        metadata.search_entry_point is not None and metadata.search_entry_point.rendered_content
        for metadata in grounding
    )
    snapshot = trace_session.closed_snapshot
    validate_trace_conformance(
        system.context.ir,
        system.plan,
        snapshot.trace,
    )
    validate_trace_closure(snapshot.trace, snapshot.closure)
    assert snapshot.closure.status == "complete"
