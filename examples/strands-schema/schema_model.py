"""Bind the generated schema to the normal Strands Bedrock model provider."""
from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from strands.models import Model

from contract4agents.adapters.strands import StrandsOutputContext
from contract4agents.adapters.strands_openai import ReasoningHistoryMode, ReasoningOpenAIModel


class ReasoningEffort(StrEnum):
    MAX = "max"


def read(reference: str) -> dict[str, str]:
    if reference != "synthetic:one":
        raise ValueError("The reference is not in this example's admitted sources")
    return {"reference": reference, "text": "The synthetic team reviews each request before publication."}


def make_model(*, model: str, options: Mapping[str, object], output: StrandsOutputContext) -> Model:
    if options:
        raise ValueError("This example has no additional model options")
    return ReasoningOpenAIModel(
        model_id=model,
        bedrock_mantle_config={"endpoint": "bedrock-runtime", "region": "us-east-1"},
        client_args={"max_retries": 0, "timeout": 900},
        params={
            "reasoning_effort": ReasoningEffort.MAX.value,
            "max_tokens": 128000,
            "service_tier": "default",
            "response_format": output.chat_completions_format(),
        },
    ).with_reasoning_history(ReasoningHistoryMode.REQUIRED)
