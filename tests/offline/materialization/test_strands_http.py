"""Raw evidence before provider decoding, with bounded streaming and private headers."""
from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator

import httpx2 as httpx
import openai
import pytest
from pydantic import JsonValue

from contract4agents.adapters.strands_openai import ReasoningOpenAIModel, RecordingOpenAITransport


class RawObserver:
    def __init__(self) -> None:
        self.events: list[dict[str, JsonValue]] = []

    def record(self, event: dict[str, JsonValue]) -> None:
        self.events.append(event)

    def body(self) -> bytes:
        return b"".join(base64.b64decode(str(event["body_base64"]))
                        for event in self.events if event["kind"] == "http_response_chunk")


@pytest.mark.asyncio
async def test_provider_error_body_and_request_identity_survive_sdk_decoding() -> None:
    observer = RawObserver()
    raw = b'{"error":{"message":"RAW-PROVIDER-ERROR","code":"denied","unmapped_field":"RETAIN"}}'
    def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=raw, headers={
            "x-amzn-requestid": "PROVIDER-REQUEST-ID", "content-type": "application/json",
            "set-cookie": "RESPONSE-SECRET", "authorization": "RESPONSE-AUTH-SECRET",
        })
    recording = RecordingOpenAITransport(observer, httpx.MockTransport(transport))
    async with httpx.AsyncClient(transport=recording) as http_client:
        async with openai.AsyncOpenAI(api_key="REQUEST-AUTH-SECRET", base_url="https://provider.invalid",
                                      max_retries=0, http_client=http_client) as client:
            model = ReasoningOpenAIModel(client=client, model_id="synthetic")
            with pytest.raises(openai.PermissionDeniedError):
                async for _event in model.stream([{"role": "user", "content": [{"text": "INPUT"}]}]):
                    pass
    assert observer.body() == raw
    kinds = [event["kind"] for event in observer.events]
    assert kinds == ["http_request", "http_response", "http_response_chunk", "http_response_complete"]
    assert observer.events[1]["headers"] == {
        "content-type": "application/json", "content-length": str(len(raw)),
        "x-amzn-requestid": "PROVIDER-REQUEST-ID",
    }
    assert len({event["event_id"] for event in observer.events}) == 4
    assert len({event["exchange_id"] for event in observer.events}) == 1
    encoded = json.dumps(observer.events)
    for secret in ("REQUEST-AUTH-SECRET", "RESPONSE-SECRET", "RESPONSE-AUTH-SECRET"):
        assert secret not in encoded


@pytest.mark.asyncio
async def test_unmapped_refusal_and_provider_fields_remain_in_raw_stream() -> None:
    observer = RawObserver()
    raw = b'data: {"id":"provider-1","choices":[{"index":0,"delta":{"refusal":"RAW-REFUSAL"},' \
          b'"finish_reason":"stop"}],"unknown_provider_field":"RETAIN"}\n\ndata: [DONE]\n\n'
    def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "text/event-stream"})
    recording = RecordingOpenAITransport(observer, httpx.MockTransport(transport))
    async with httpx.AsyncClient(transport=recording) as http_client:
        async with openai.AsyncOpenAI(api_key="synthetic", base_url="https://provider.invalid", max_retries=0,
                                      http_client=http_client) as client:
            model = ReasoningOpenAIModel(client=client, model_id="synthetic")
            events = [event async for event in model.stream([{"role": "user", "content": [{"text": "INPUT"}]}])]
    assert observer.body() == raw
    assert "RAW-REFUSAL" not in json.dumps(events)
    assert observer.events[-1]["kind"] == "http_response_complete"


@pytest.mark.asyncio
async def test_stream_does_not_read_ahead_and_partial_failure_has_no_complete_marker() -> None:
    observer = RawObserver()
    reads: list[int] = []
    closes: list[int] = []
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            reads.append(1)
            yield b"\xffPARTIAL"
            reads.append(2)
            raise httpx.ReadError("INJECTED-TRUNCATION")
        async def aclose(self) -> None:
            closes.append(1)
    def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=BrokenStream(), headers={"content-type": "text/event-stream"})
    recording = RecordingOpenAITransport(observer, httpx.MockTransport(transport))
    async with httpx.AsyncClient(transport=recording) as client:
        url = "https://user:pass@provider.invalid/x?key=URL-SECRET"
        async with client.stream("POST", url, content=b"INPUT") as response:
            assert reads == []
            iterator = response.aiter_raw()
            assert await anext(iterator) == b"\xffPARTIAL"
            assert reads == [1]
            with pytest.raises(httpx.ReadError):
                await anext(iterator)
    assert observer.body() == b"\xffPARTIAL"
    assert observer.events[-1]["kind"] == "http_failed"
    assert not any(event["kind"] == "http_response_complete" for event in observer.events)
    assert closes == [1]
    assert observer.events[0]["url"] == "https://provider.invalid/x"


@pytest.mark.asyncio
async def test_request_transport_failure_is_retained_without_response() -> None:
    observer = RawObserver()
    def transport(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("INJECTED-CONNECT-FAILURE")
    recording = RecordingOpenAITransport(observer, httpx.MockTransport(transport))
    async with httpx.AsyncClient(transport=recording) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post("https://provider.invalid", content=b"INPUT")
    assert [event["kind"] for event in observer.events] == ["http_request", "http_failed"]
    assert observer.events[-1]["phase"] == "request"


@pytest.mark.asyncio
async def test_cancelled_stream_retains_received_bytes_and_closes_without_completion() -> None:
    observer = RawObserver()
    closes: list[int] = []
    class CancelledStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"BEFORE-CANCEL"
            raise asyncio.CancelledError
        async def aclose(self) -> None:
            closes.append(1)
    def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=CancelledStream())
    recording = RecordingOpenAITransport(observer, httpx.MockTransport(transport))
    async with httpx.AsyncClient(transport=recording) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.get("https://provider.invalid")
    assert observer.body() == b"BEFORE-CANCEL"
    assert observer.events[-1]["error_type"] == "CancelledError"
    assert not any(event["kind"] == "http_response_complete" for event in observer.events)
    assert closes == [1]
