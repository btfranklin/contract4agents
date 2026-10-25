from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import IO

from contract4agents._strict_json import json_object


def test_language_server_stdio_handshake_and_navigation(tmp_path: Path) -> None:
    source = """\
type Input:
    topic: string

type Output:
    summary: string

tool research.search(query: string) -> Output:
    description = "Search."
    side_effect = false

agent Researcher(request: Input) -> Output:
    use research.search:
        availability = enabled
        authorization = preapproved
        execution = provider_hosted
"""
    path = tmp_path / "research.contract"
    path.write_text(source)
    process = subprocess.Popen(
        [sys.executable, "-m", "contract4agents.language_server"],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    messages: queue.Queue[dict[str, object]] = queue.Queue()
    threading.Thread(target=_read_messages, args=(process.stdout, messages), daemon=True).start()

    try:
        _send(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "processId": None,
                    "rootUri": tmp_path.as_uri(),
                    "workspaceFolders": [{"uri": tmp_path.as_uri(), "name": "fixture"}],
                    "capabilities": {
                        "textDocument": {
                            "rename": {"prepareSupport": True},
                        }
                    },
                },
            },
        )
        initialized = _response(messages, 1)
        capabilities = _object(_object(initialized["result"])["capabilities"])
        assert capabilities["hoverProvider"] is True
        assert capabilities["definitionProvider"] is True
        assert capabilities["referencesProvider"] is True
        assert _object(capabilities["renameProvider"])["prepareProvider"] is True
        assert "completionProvider" in capabilities
        assert capabilities["documentHighlightProvider"] is True
        assert capabilities["documentSymbolProvider"] is True
        assert capabilities["workspaceSymbolProvider"]
        assert "codeActionProvider" in capabilities
        assert "inlayHintProvider" in capabilities
        assert "semanticTokensProvider" in capabilities

        _send(process.stdin, {"jsonrpc": "2.0", "method": "initialized", "params": {}})
        _send(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": path.as_uri(),
                        "languageId": "contract4agents",
                        "version": 1,
                        "text": source,
                    }
                },
            },
        )
        _send(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "textDocument/hover",
                "params": {
                    "textDocument": {"uri": path.as_uri()},
                    "position": _lsp_position(source, "provider_hosted"),
                },
            },
        )
        hover = _response(messages, 2)
        assert "provider executes" in _text(_object(_object(hover["result"])["contents"])["value"])

        _send(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "textDocument/definition",
                "params": {
                    "textDocument": {"uri": path.as_uri()},
                    "position": _lsp_position(source, "Input", occurrence=1),
                },
            },
        )
        definition = _response(messages, 3)["result"]
        assert definition == [
            {
                "uri": path.as_uri(),
                "range": {
                    "start": {"line": 0, "character": 5},
                    "end": {"line": 0, "character": 10},
                },
            }
        ]

        references = _request(
            process.stdin,
            messages,
            4,
            "textDocument/references",
            {
                "textDocument": {"uri": path.as_uri()},
                "position": _lsp_position(source, "Input", occurrence=1),
                "context": {"includeDeclaration": True},
            },
        )
        assert len(_array(references)) == 2

        highlights = _request(
            process.stdin,
            messages,
            5,
            "textDocument/documentHighlight",
            {
                "textDocument": {"uri": path.as_uri()},
                "position": _lsp_position(source, "Input", occurrence=1),
            },
        )
        assert len(_array(highlights)) == 2

        prepare_rename = _request(
            process.stdin,
            messages,
            6,
            "textDocument/prepareRename",
            {
                "textDocument": {"uri": path.as_uri()},
                "position": _lsp_position(source, "Input", occurrence=1),
            },
        )
        assert _object(prepare_rename)["start"] == {"line": 10, "character": 26}

        rename = _request(
            process.stdin,
            messages,
            7,
            "textDocument/rename",
            {
                "textDocument": {"uri": path.as_uri()},
                "position": _lsp_position(source, "Input", occurrence=1),
                "newName": "ResearchInput",
            },
        )
        assert len(_array(_object(_object(rename)["changes"])[path.as_uri()])) == 2

        completion = _request(
            process.stdin,
            messages,
            8,
            "textDocument/completion",
            {
                "textDocument": {"uri": path.as_uri()},
                "position": _lsp_position_after(source, "authorization = "),
            },
        )
        assert {_object(item)["label"] for item in _array(_object(completion)["items"])} == {
            "approval_required",
            "preapproved",
        }

        document_symbols = _request(
            process.stdin,
            messages,
            9,
            "textDocument/documentSymbol",
            {"textDocument": {"uri": path.as_uri()}},
        )
        assert [_object(item)["name"] for item in _array(document_symbols)] == [
            "Input", "Output", "research.search", "Researcher"
        ]
        assert _object(_array(_object(_array(document_symbols)[0])["children"])[0])["name"] == "topic"

        workspace_symbols = _request(
            process.stdin,
            messages,
            10,
            "workspace/symbol",
            {"query": "research"},
        )
        assert {_text(_object(item)["name"]) for item in _array(workspace_symbols)} == {"research.search", "Researcher"}

        semantic_tokens = _request(
            process.stdin,
            messages,
            11,
            "textDocument/semanticTokens/full",
            {"textDocument": {"uri": path.as_uri()}},
        )
        assert _object(semantic_tokens)["data"]
        assert len(_array(_object(semantic_tokens)["data"])) % 5 == 0

        inlay_hints = _request(
            process.stdin,
            messages,
            12,
            "textDocument/inlayHint",
            {
                "textDocument": {"uri": path.as_uri()},
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": source.count("\n"), "character": 0},
                },
            },
        )
        assert [_object(item)["label"] for item in _array(inlay_hints)] == ["  (query: string) -> Output"]

        missing_hover = _request(
            process.stdin,
            messages,
            13,
            "textDocument/hover",
            {
                "textDocument": {"uri": path.as_uri()},
                "position": {"line": 12, "character": 0},
            },
        )
        assert missing_hover is None

        broken_source = source.replace(
            "        authorization = preapproved\n        execution = provider_hosted\n",
            "",
        )
        _send(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didChange",
                "params": {
                    "textDocument": {"uri": path.as_uri(), "version": 2},
                    "contentChanges": [{"text": broken_source}],
                },
            },
        )
        published = _notification(
            messages,
            "textDocument/publishDiagnostics",
            lambda item: any(
                _object(diagnostic).get("code") == "SEM107"
                for diagnostic in _array(_object(item["params"])["diagnostics"])
            ),
        )
        authorization_diagnostic = next(
            _object(item)
            for item in _array(_object(published["params"])["diagnostics"])
            if _object(item).get("code") == "SEM107"
        )
        code_actions = _request(
            process.stdin,
            messages,
            14,
            "textDocument/codeAction",
            {
                "textDocument": {"uri": path.as_uri()},
                "range": authorization_diagnostic["range"],
                "context": {"diagnostics": [authorization_diagnostic]},
            },
        )
        assert {_text(_object(item)["title"]) for item in _array(code_actions)} == {
            "Set authorization to approval_required",
            "Set authorization to preapproved",
        }

        _send(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didSave",
                "params": {"textDocument": {"uri": path.as_uri()}},
            },
        )
        _send(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didClose",
                "params": {"textDocument": {"uri": path.as_uri()}},
            },
        )

        _send(process.stdin, {"jsonrpc": "2.0", "id": 15, "method": "shutdown", "params": None})
        assert _response(messages, 15)["result"] is None
        _send(process.stdin, {"jsonrpc": "2.0", "method": "exit", "params": None})
        process.wait(timeout=5)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def _object(value: object) -> dict[str, object]:
    return dict(json_object("JSON-RPC object", value))


def _array(value: object) -> list[object]:
    assert isinstance(value, list), value
    return value


def _text(value: object) -> str:
    assert isinstance(value, str), value
    return value


def _send(stream: IO[bytes], message: dict[str, object]) -> None:
    body = json.dumps(message, separators=(",", ":")).encode()
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    stream.flush()


def _read_messages(stream: IO[bytes], messages: queue.Queue[dict[str, object]]) -> None:
    while True:
        headers: dict[str, str] = {}
        while line := stream.readline():
            if line == b"\r\n":
                break
            name, value = line.decode().split(":", 1)
            headers[name.lower()] = value.strip()
        if not headers:
            return
        body = stream.read(int(headers["content-length"]))
        messages.put(dict(json_object("JSON-RPC message", json.loads(body))))


def _response(messages: queue.Queue[dict[str, object]], request_id: int) -> dict[str, object]:
    while True:
        message = messages.get(timeout=10)
        if message.get("id") == request_id:
            assert "error" not in message, message["error"]
            return message


def _request(
    stream: IO[bytes],
    messages: queue.Queue[dict[str, object]],
    request_id: int,
    method: str,
    params: dict[str, object],
) -> object:
    _send(stream, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    return _response(messages, request_id)["result"]


def _notification(
    messages: queue.Queue[dict[str, object]],
    method: str,
    predicate: Callable[[dict[str, object]], bool],
) -> dict[str, object]:
    while True:
        message = messages.get(timeout=10)
        if message.get("method") == method and predicate(message):
            return message


def _lsp_position(source: str, needle: str, *, occurrence: int = 0) -> dict[str, int]:
    offset = -1
    for _ in range(occurrence + 1):
        offset = source.index(needle, offset + 1)
    before = source[:offset]
    return {"line": before.count("\n"), "character": len(before.rsplit("\n", 1)[-1])}


def _lsp_position_after(source: str, needle: str) -> dict[str, int]:
    position = _lsp_position(source, needle)
    position["character"] += len(needle)
    return position
