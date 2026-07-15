"""OpenAI-safe name encoding helpers."""

from __future__ import annotations

_OPENAI_TOOL_NAME_PREFIX = "c4a_"


def openai_tool_name(contract_name: str) -> str:
    """Convert a Contract4Agents capability name into an OpenAI-safe tool name."""
    return _OPENAI_TOOL_NAME_PREFIX + "".join(f"{len(part)}_{part}" for part in contract_name.split("."))


def contract_tool_name(openai_name: str) -> str:
    """Convert a generated OpenAI tool name back into the Contract4Agents capability name."""
    if not openai_name.startswith(_OPENAI_TOOL_NAME_PREFIX):
        return openai_name

    encoded = openai_name[len(_OPENAI_TOOL_NAME_PREFIX) :]
    parts: list[str] = []
    cursor = 0
    while cursor < len(encoded):
        delimiter = encoded.find("_", cursor)
        if delimiter == -1 or delimiter == cursor:
            raise ValueError(f"OpenAI tool name `{openai_name}` is not valid Contract4Agents encoding")
        raw_length = encoded[cursor:delimiter]
        if not raw_length.isdigit():
            raise ValueError(f"OpenAI tool name `{openai_name}` is not valid Contract4Agents encoding")
        length = int(raw_length)
        start = delimiter + 1
        end = start + length
        if end > len(encoded):
            raise ValueError(f"OpenAI tool name `{openai_name}` is not valid Contract4Agents encoding")
        parts.append(encoded[start:end])
        cursor = end
    return ".".join(parts)


__all__ = ["contract_tool_name", "openai_tool_name"]
