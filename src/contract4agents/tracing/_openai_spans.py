"""Semantic classification for OpenAI Agents SDK trace spans."""

from __future__ import annotations

from contract4agents.adapters._openai_names import contract_tool_name
from contract4agents.ir import CanonicalIR, SemanticId, semantic_id
from contract4agents.tracing._models import TraceSemanticRefs
from contract4agents.tracing._openai_utils import optional_text_attr


class OpenAISpanMapper:
    """Map provider span structure onto canonical semantic identities."""

    def __init__(self, ir: CanonicalIR) -> None:
        self.ir = ir
        self._parents: dict[str, str | None] = {}
        self._semantics: dict[str, TraceSemanticRefs] = {}

    def register(
        self,
        span_id: str,
        parent_id: str | None,
        semantic: TraceSemanticRefs,
    ) -> None:
        self._parents[span_id] = parent_id
        self._semantics[span_id] = semantic

    def semantic_for(self, span_id: str) -> TraceSemanticRefs | None:
        return self._semantics.get(span_id)

    def classify(self, span: object, *, completed: bool) -> tuple[str, TraceSemanticRefs]:
        data = getattr(span, "span_data", None)
        span_type = str(getattr(data, "type", "custom"))
        suffix = "completed" if completed else "started"
        if span_type == "agent":
            name = str(getattr(data, "name", ""))
            agent_id = semantic_id("agent", name)
            return f"agent.{suffix}", TraceSemanticRefs(
                agent_id=agent_id if agent_id in self.ir.agents else None
            )
        if span_type == "function":
            raw_name = str(getattr(data, "name", ""))
            try:
                name = contract_tool_name(raw_name)
            except ValueError:
                name = raw_name
            parent_agent = self.parent_agent(span)
            edge = next(
                (
                    item
                    for item in self.ir.composition.values()
                    if item.name == name
                    and (parent_agent is None or item.source_agent_id == parent_agent)
                ),
                None,
            )
            if edge is not None:
                return f"composition.{suffix}", TraceSemanticRefs(
                    agent_id=edge.source_agent_id,
                    composition_id=edge.id,
                    isolation_id=edge.isolation_id,
                )
            capability_id: SemanticId | None = semantic_id("tool", name)
            if capability_id not in self.ir.capabilities:
                capability_id = None
            grant_id = None
            if parent_agent is not None and capability_id is not None:
                candidate = semantic_id(
                    "grant",
                    parent_agent.parts[0],
                    capability_id.parts[0],
                )
                grant_id = candidate if candidate in self.ir.grants else None
            return f"tool.{suffix}", TraceSemanticRefs(
                agent_id=parent_agent,
                capability_id=capability_id,
                grant_id=grant_id,
            )
        if span_type == "handoff":
            source = str(getattr(data, "from_agent", ""))
            target = str(getattr(data, "to_agent", ""))
            edge = next(
                (
                    item
                    for item in self.ir.composition.values()
                    if item.mode == "handoff"
                    and item.source_agent_id == semantic_id("agent", source)
                    and item.target_agent_id == semantic_id("agent", target)
                ),
                None,
            )
            return f"handoff.{suffix}", TraceSemanticRefs(
                agent_id=semantic_id("agent", source) if source else None,
                composition_id=edge.id if edge is not None else None,
                isolation_id=edge.isolation_id if edge is not None else None,
            )
        return f"provider.{span_type}.{suffix}", TraceSemanticRefs(
            agent_id=self.parent_agent(span)
        )

    def parent_agent(self, span: object) -> SemanticId | None:
        parent_id = optional_text_attr(span, "parent_id")
        visited: set[str] = set()
        while parent_id is not None and parent_id not in visited:
            visited.add(parent_id)
            semantic = self._semantics.get(parent_id)
            if semantic is not None and semantic.agent_id is not None:
                return semantic.agent_id
            parent_id = self._parents.get(parent_id)
        return None


__all__ = ["OpenAISpanMapper"]
