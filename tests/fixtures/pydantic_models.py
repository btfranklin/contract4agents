from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


class ResearchPriority(StrEnum):
    LOW = "low"
    HIGH = "high"


class ResearchSourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    confidence: float = Field(ge=0.0, le=1.0)


class ResearchPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    mode: Literal["quick", "deep"] = "quick"
    priority: ResearchPriority
    tags: list[str] = Field(default_factory=list)
    constraints: dict[str, str] = Field(default_factory=dict)
    source: ResearchSourceModel | None = None


class ResearchSummaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    source_count: int = Field(ge=0)
    plan: ResearchPlanModel


class NotPydantic:
    pass


class RootListModel(RootModel[list[str]]):
    pass
