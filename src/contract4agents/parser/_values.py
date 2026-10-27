"""Small value-parsing helpers for Contract4Agents source transformers."""

from __future__ import annotations

import json


def coerce_name_list(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return parse_name_list(str(value))


def clean_list_item(raw: str) -> str:
    return unquote(raw.rstrip(",").strip())


def split_default(raw: str) -> tuple[str, str | None]:
    depth = 0
    in_string = False
    quote = ""
    for index, char in enumerate(raw):
        if char in {"'", '"'} and (not in_string or char == quote):
            in_string = not in_string
            quote = char if in_string else ""
        elif not in_string and char in "([":
            depth += 1
        elif not in_string and char in ")]":
            depth -= 1
        elif char == "=" and depth == 0 and not in_string:
            return raw[:index].strip(), raw[index + 1 :].strip()
    return raw.strip(), None


def split_csv(raw: str) -> list[str]:
    if not raw.strip():
        return []
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    quote = ""
    for char in raw:
        if char in {"'", '"'} and (not in_string or char == quote):
            in_string = not in_string
            quote = char if in_string else ""
        elif not in_string and char in "([":
            depth += 1
        elif not in_string and char in ")]":
            depth -= 1
        if char == "," and depth == 0 and not in_string:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return [part for part in parts if part]


def parse_name_list(raw: str) -> list[str]:
    stripped = raw.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return []
    return [unquote(item.strip()) for item in split_csv(stripped[1:-1])]


def unquote(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def unquote_json_string(value: str) -> str:
    """Decode a double-quoted JSON string; leave other scalar forms unchanged."""

    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        decoded = json.loads(stripped)
        if not isinstance(decoded, str):
            raise ValueError("A double-quoted agent goal must be a JSON string")
        return decoded
    return unquote(stripped)
