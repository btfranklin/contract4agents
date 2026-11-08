"""Generated output configuration and validation for native Strands agents."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

if TYPE_CHECKING:
    from contract4agents.materialization import MaterializedSystem


class StrandsOutputMode(StrEnum):
    """Supported output paths. Schema mode requires a native schema model factory."""

    TOOL = "tool"
    SCHEMA = "schema"


def parse_strands_output_mode(value: object) -> StrandsOutputMode:
    if not isinstance(value, str):
        raise ValueError("Strands output_mode must be tool or schema")
    return StrandsOutputMode(value)


@dataclass(frozen=True)
class StrandsOutputContext:
    """The generated contract output supplied to a schema-mode model factory."""

    agent_name: str
    output_type: object

    @property
    def schema(self) -> dict[str, object]:
        """Return the full contract schema. Host validation retains all bounds."""
        return dict(TypeAdapter(self.output_type).json_schema())

    def chat_completions_format(self) -> dict[str, object]:
        """Return Bedrock-compatible strict native Chat Completions configuration."""
        from contract4agents.materialization._harness_schema import bedrock_output_schema

        return {
            "type": "json_schema",
            "json_schema": {
                "name": self.agent_name,
                "strict": True,
                "schema": bedrock_output_schema(self.schema, strict=True),
            },
        }


def _json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Native output contains a duplicate JSON member")
        result[key] = value
    return result


def _nonfinite(_value: str) -> object:
    raise ValueError("Native output contains a non-finite JSON number")


def extract_strands_output(system: MaterializedSystem, agent: object, result: object) -> object:
    """Extract one terminal native result and validate its full contract type."""
    selected = next((identifier for identifier, native in system.graph.agents.items() if native is agent), None)
    if selected is None:
        raise ValueError("The agent is not part of this materialized system")
    mode = parse_strands_output_mode(system.plan.agents[selected].model_options.get("output_mode", "tool"))
    if mode == StrandsOutputMode.TOOL:
        return system.validate_output_for_agent(agent, getattr(result, "structured_output", None))
    if getattr(result, "stop_reason", None) not in {"end_turn", "stop_sequence"}:
        raise ValueError("Native schema output requires a complete final response")
    message = getattr(result, "message", None)
    if not isinstance(message, Mapping) or message.get("role") != "assistant":
        raise ValueError("Native schema output requires an assistant message")
    content = message.get("content")
    if not isinstance(content, list) or not content:
        raise ValueError("Native schema output requires JSON text")
    parts: list[str] = []
    for block in content:
        if not isinstance(block, Mapping):
            raise ValueError("Native schema output contains an invalid content block")
        if set(block) == {"text"} and isinstance(block["text"], str):
            parts.append(block["text"])
        elif set(block) != {"reasoningContent"}:
            raise ValueError("Native schema output contains non-text result content")
    value: object = json.loads("".join(parts), object_pairs_hook=_json_pairs, parse_constant=_nonfinite)
    return system.validate_output_for_agent(agent, value)
