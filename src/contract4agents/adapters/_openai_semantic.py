"""OpenAI-backed semantic judge."""

from __future__ import annotations

import os
from typing import Any

from contract4agents.adapters._openai_types import OpenAIAdapterUnavailable


class OpenAISemanticJudge:
    def __init__(self, model: str = "gpt-5.5", api_key_env: str = "OPENAI_API_KEY") -> None:
        self.model = model
        self.api_key_env = api_key_env

    async def judge(self, *, output: dict[str, Any], criterion: str) -> bool:
        if not os.getenv(self.api_key_env):
            raise OpenAIAdapterUnavailable(f"{self.api_key_env} is not set")
        try:
            from openai import AsyncOpenAI
        except Exception as exc:  # noqa: BLE001 - optional adapter import boundary.
            raise OpenAIAdapterUnavailable("openai package is not installed") from exc
        client = AsyncOpenAI()
        response = await client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": "Return only PASS or FAIL. Evaluate whether the output satisfies the criterion.",
                },
                {
                    "role": "user",
                    "content": f"Criterion: {criterion}\nOutput: {output}",
                },
            ],
        )
        text = getattr(response, "output_text", "")
        return str(text).strip().upper() == "PASS"


__all__ = ["OpenAISemanticJudge"]
