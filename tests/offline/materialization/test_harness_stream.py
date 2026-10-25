from __future__ import annotations

import pytest
from botocore.session import get_session  # type: ignore[import-untyped]
from botocore.validate import ParamValidator  # type: ignore[import-untyped]

from contract4agents.materialization._harness_stream import HarnessStream
from tests.support.agentcore_harness import success_stream, tool_stream


def _text_message(text: str) -> list[dict[str, object]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]


def _tool_start(name: str, tool_use_id: str) -> list[dict[str, object]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"name": name, "toolUseId": tool_use_id}},
            }
        },
    ]


def test_text_stream_accepts_converse_deltas_without_content_block_start() -> None:
    events: list[dict[str, object]] = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "first "}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "second"}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]

    stream = HarnessStream.read(events)

    assert stream.failure is None
    assert stream.text == "first second"
    assert stream.stop_reason == "end_turn"


def test_only_the_last_complete_message_supplies_output_text() -> None:
    events = [
        *_text_message("discard this"),
        *_text_message("final message"),
    ]

    stream = HarnessStream.read(events)

    assert stream.failure is None
    assert stream.text == "final message"


def test_tool_input_fragments_are_not_available_until_complete_tool_stop() -> None:
    partial = _tool_start("lookup", "call-9") + [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"x":'}}}},
    ]

    stream = HarnessStream.read(partial)

    assert stream.failure == "incomplete_stream"
    assert stream.tool_name is None
    assert stream.arguments is None


def test_complete_tool_use_combines_fragments_and_returns_one_call() -> None:
    events = _tool_start("lookup", "call-9") + [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"query":'}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '"records"}'}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]

    stream = HarnessStream.read(events)

    assert stream.failure is None
    assert stream.tool_name == "lookup"
    assert stream.tool_use_id == "call-9"
    assert stream.arguments == {"query": "records"}


def test_multiple_complete_tool_calls_return_only_the_first_call() -> None:
    events = _tool_start("first", "call-1") + [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"x":1}'}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 1,
                "start": {"toolUse": {"name": "second", "toolUseId": "call-2"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"x":2}'}}}},
        {"contentBlockStop": {"contentBlockIndex": 1}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]

    stream = HarnessStream.read(events)

    assert stream.failure is None
    assert (stream.tool_name, stream.tool_use_id, stream.arguments) == ("first", "call-1", {"x": 1})


def test_duplicate_tool_use_ids_fail_closed() -> None:
    events = _tool_start("first", "call-1") + [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": "{}"}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 1,
                "start": {"toolUse": {"name": "second", "toolUseId": "call-1"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": "{}"}}}},
        {"contentBlockStop": {"contentBlockIndex": 1}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]

    stream = HarnessStream.read(events)

    assert stream.failure == "invalid_stream"
    assert stream.tool_name is None
    assert stream.arguments is None


def test_hook_stopped_is_a_complete_non_tool_stop_reason() -> None:
    stream = HarnessStream.read(
        [
            {"messageStart": {"role": "assistant"}},
            {"messageStop": {"stopReason": "hook_stopped"}},
        ]
    )

    assert stream.failure is None
    assert stream.stop_reason == "hook_stopped"


def test_known_hook_event_is_ignored_without_changing_stop_reason() -> None:
    stream = HarnessStream.read(
        [
            {"messageStart": {"role": "assistant"}},
            {
                "hookEvent": {
                    "hookEventId": "hook-1",
                    "name": "application-hook",
                    "type": "before_invocation",
                    "decision": "allow",
                }
            },
            {"messageStop": {"stopReason": "end_turn"}},
        ]
    )

    assert stream.failure is None
    assert stream.stop_reason == "end_turn"


def test_provider_error_after_a_final_message_invalidates_the_stream() -> None:
    stream = HarnessStream.read(
        [
            *_text_message('{"answer": "done"}'),
            {"internalServerException": {"message": "late stream failure"}},
        ]
    )

    assert stream.failure == "provider_error"
    assert stream.text == '{"answer": "done"}'


@pytest.mark.parametrize(
    "events",
    [
        [{"messageStart": {"role": "assistant"}}],
        [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "open"}}},
        ],
        _tool_start("lookup", "call-1")
        + [
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": "{}"}}}},
            {"messageStop": {"stopReason": "tool_use"}},
        ],
    ],
)
def test_unstopped_streams_and_open_blocks_fail_closed(
    events: list[dict[str, object]],
) -> None:
    stream = HarnessStream.read(events)

    assert stream.failure == "incomplete_stream"
    assert stream.tool_name is None
    assert stream.arguments is None


@pytest.mark.parametrize(
    ("events", "failure"),
    [
        ([{"messageStart": {"role": "assistant"}}, {"mystery": {}}], "unsupported_stream_event"),
        (
            [
                {"messageStart": {"role": "assistant"}},
                {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": 3}}},
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "end_turn"}},
            ],
            "invalid_stream",
        ),
        (
            [
                {"messageStart": {"role": "assistant"}},
                {
                    "contentBlockStart": {
                        "contentBlockIndex": 0,
                        "start": {"toolUse": {"name": "lookup/tool", "toolUseId": "call-1"}},
                    }
                },
            ],
            "invalid_stream",
        ),
        (
            [
                {"messageStart": {"role": "assistant"}},
                {
                    "contentBlockStart": {
                        "contentBlockIndex": 0,
                        "start": {
                            "toolUse": {"name": "lookup", "toolUseId": "call-1"},
                            "unknown": {},
                        },
                    }
                },
            ],
            "invalid_stream",
        ),
        (
            _tool_start("lookup", "call-1")
            + [
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 0,
                        "delta": {"toolUse": {"input": "{}", "unknown": True}},
                    }
                },
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "tool_use"}},
            ],
            "invalid_stream",
        ),
        (
            [
                {"messageStart": {"role": "assistant"}},
                {"messageStop": {"stopReason": "unknown"}},
            ],
            "invalid_stream",
        ),
        (
            [
                {"messageStart": {"role": "assistant"}},
                {
                    "contentBlockStart": {
                        "contentBlockIndex": 0,
                        "start": {"toolUse": {"name": "", "toolUseId": "call-1"}},
                    }
                },
                {"messageStop": {"stopReason": "tool_use"}},
            ],
            "invalid_stream",
        ),
        (
            [
                {"messageStart": {"role": "assistant"}},
                {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 0,
                        "delta": {"text": "text", "toolUse": {"input": "{}"}},
                    }
                },
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "end_turn"}},
            ],
            "invalid_stream",
        ),
        (
            _tool_start("lookup", "call-1")
            + [
                {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": "{}"}}}},
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "end_turn"}},
            ],
            "invalid_stream",
        ),
    ],
)
def test_malformed_and_mixed_events_are_rejected(
    events: list[dict[str, object]],
    failure: str,
) -> None:
    stream = HarnessStream.read(events)

    assert stream.failure == failure
    assert stream.tool_name is None
    assert stream.arguments is None


@pytest.mark.parametrize(
    "payload",
    [
        '{"value": 1, "value": 2}',
        '{"value": NaN}',
        '{"value": Infinity}',
    ],
)
def test_duplicate_json_keys_and_non_finite_tool_values_are_rejected(payload: str) -> None:
    events = _tool_start("lookup", "call-1") + [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": payload}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]

    stream = HarnessStream.read(events)

    assert stream.failure == "invalid_tool_input"
    assert stream.tool_name is None
    assert stream.arguments is None


def test_reasoning_blocks_do_not_change_visible_text() -> None:
    events: list[dict[str, object]] = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "visible"}}},
        {
            "contentBlockDelta": {
                "contentBlockIndex": 1,
                "delta": {
                    "reasoningContent": {
                        "redactedContent": b"private reasoning",
                        "signature": "signature",
                    }
                },
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"contentBlockStop": {"contentBlockIndex": 1}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]

    stream = HarnessStream.read(events)

    assert stream.failure is None
    assert stream.text == "visible"


def test_duplicate_usage_is_deduplicated_but_distinct_usage_stays_separate() -> None:
    first = {"inputTokens": 5, "outputTokens": 2, "totalTokens": 7}
    second = {"inputTokens": 8, "outputTokens": 1, "totalTokens": 9}
    stream = HarnessStream.read(
        [
            {"messageStart": {"role": "assistant"}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": first, "metrics": {"latencyMs": 10}}},
            {"metadata": {"usage": first, "metrics": {"latencyMs": 10}}},
            {"metadata": {"usage": second, "metrics": {"latencyMs": 11}}},
        ]
    )

    assert stream.failure is None
    assert stream.usage == [first, second]


def test_stream_size_limit_applies_before_event_processing() -> None:
    huge_text = "x" * (8 * 1024 * 1024)
    with pytest.raises(ValueError, match="size limit"):
        HarnessStream.read(
            [
                {"messageStart": {"role": "assistant"}},
                {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": huge_text}}},
            ]
        )


def test_test_events_match_installed_invoke_harness_stream_shapes() -> None:
    service = get_session().get_service_model("bedrock-agentcore")
    stream_shape = service.operation_model("InvokeHarness").output_shape.members["stream"]
    event_groups = [
        success_stream("ok", metadata={"inputTokens": 1, "outputTokens": 1}),
        tool_stream("lookup", {}, metadata={"inputTokens": 1, "outputTokens": 1}),
    ]

    for events in event_groups:
        for event in events:
            event_name, payload = next(iter(event.items()))
            assert event_name in stream_shape.members
            shape = stream_shape.members[event_name]
            errors = ParamValidator().validate(payload, shape)
            assert not errors.has_errors(), errors.generate_report()
