"""Record raw provider HTTP evidence through the public HTTPX transport API."""
from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Literal, Protocol
from uuid import uuid4

import httpx2 as httpx
from pydantic import JsonValue

_RESPONSE_HEADERS = frozenset({
    "content-type", "x-request-id", "x-amzn-requestid", "x-amz-request-id", "request-id",
    "x-amzn-trace-id", "retry-after", "content-encoding", "content-length", "transfer-encoding",
    "x-amzn-errortype", "x-amzn-error-type",
})


class StrandsHTTPObserver(Protocol):
    """A host-owned durable sink for JSON-safe provider HTTP events."""

    def record(self, event: dict[str, JsonValue]) -> None: ...


HTTPEventKind = Literal[
    "http_request", "http_response", "http_response_chunk", "http_response_complete", "http_failed",
]


def _record(observer: StrandsHTTPObserver, exchange: str, kind: HTTPEventKind, **fields: JsonValue) -> None:
    observer.record({"event_id": str(uuid4()), "exchange_id": exchange, "kind": kind, **fields})


def _encoded(body: bytes) -> str:
    return base64.b64encode(body).decode("ascii")


class _RecordingStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, observer: StrandsHTTPObserver, exchange: str) -> None:
        self.stream = stream
        self.observer = observer
        self.exchange = exchange
        self.iterator = self._iterate()

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self.iterator

    async def _iterate(self) -> AsyncGenerator[bytes, None]:
        count = 0
        try:
            async for chunk in self.stream:
                _record(self.observer, self.exchange, "http_response_chunk", chunk_index=count,
                        body_base64=_encoded(chunk))
                count += 1
                yield chunk
        except BaseException as error:
            _record(self.observer, self.exchange, "http_failed", phase="stream", error_type=type(error).__name__)
            raise
        else:
            _record(self.observer, self.exchange, "http_response_complete", chunk_count=count)

    async def aclose(self) -> None:
        try:
            await self.iterator.aclose()
        finally:
            await self.stream.aclose()


class RecordingOpenAITransport(httpx.AsyncBaseTransport):
    """Tee response bytes before SDK decoding without reading ahead or changing them.

    Request headers are never recorded. Response headers use a fixed allowlist.
    URL credentials and query parameters are removed from the evidence URL.
    Bodies remain private host evidence and can contain sensitive application data.
    """

    def __init__(self, observer: StrandsHTTPObserver, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.observer = observer
        self.transport = transport if transport is not None else httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        exchange = str(uuid4())
        try:
            body = await request.aread()
            _record(self.observer, exchange, "http_request", method=request.method,
                    url=str(request.url.copy_with(username="", password="", query=None)), body_base64=_encoded(body))
            response = await self.transport.handle_async_request(request)
            headers: dict[str, JsonValue] = {
                key: value for key, value in response.headers.items() if key.lower() in _RESPONSE_HEADERS
            }
            _record(self.observer, exchange, "http_response", status_code=response.status_code, headers=headers)
            if not isinstance(response.stream, httpx.AsyncByteStream):
                raise TypeError("The provider response must use an asynchronous byte stream")
            return httpx.Response(
                response.status_code, headers=response.headers,
                stream=_RecordingStream(response.stream, self.observer, exchange), extensions=response.extensions,
            )
        except BaseException as error:
            _record(self.observer, exchange, "http_failed", phase="request", error_type=type(error).__name__)
            raise

    async def aclose(self) -> None:
        await self.transport.aclose()
