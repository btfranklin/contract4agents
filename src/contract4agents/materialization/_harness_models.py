"""Typed Harness invocation and continuation values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from contract4agents._strict_json import json_object, json_string
from contract4agents.ir import FrozenJsonValue, FrozenMap
from contract4agents.materialization._harness_configuration import content_digest, frozen_object, thaw
from contract4agents.tracing import TraceAttempt


@dataclass(frozen=True)
class HarnessRunContext:
    """Trusted identity and session scope supplied by the application."""

    session_id: str
    principal_id: str
    authorization_context: str
    run_id: str
    thread_id: str
    attempt: TraceAttempt
    parent_event_id: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{32,99}", self.session_id):
            raise ValueError("Harness session IDs require 33 to 100 letters, digits, underscores, or hyphens")
        for value in (self.principal_id, self.authorization_context, self.run_id, self.thread_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Harness application identity and run scope must be explicit")
        if not isinstance(self.attempt, TraceAttempt):
            raise TypeError("Harness invocation requires a trace attempt")

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "principal_id": self.principal_id,
            "authorization_context": self.authorization_context,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "attempt": self.attempt.to_dict(),
            "parent_event_id": self.parent_event_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> HarnessRunContext:
        raw = json_object("Harness state", thaw(frozen_object(value)))
        if set(raw) != {
            "session_id",
            "principal_id",
            "authorization_context",
            "run_id",
            "thread_id",
            "attempt",
            "parent_event_id",
        }:
            raise ValueError("Invalid Harness context keys")
        parent = raw["parent_event_id"]
        if parent is not None and not isinstance(parent, str):
            raise TypeError("Harness parent event ID must be a string")
        return cls(
            session_id=json_string("session_id", raw["session_id"]),
            principal_id=json_string("principal_id", raw["principal_id"]),
            authorization_context=json_string("authorization_context", raw["authorization_context"]),
            run_id=json_string("run_id", raw["run_id"]),
            thread_id=json_string("thread_id", raw["thread_id"]),
            attempt=TraceAttempt.from_dict(raw["attempt"]),
            parent_event_id=parent,
        )


@dataclass(frozen=True)
class HarnessPendingCall:
    """One complete provider handoff bound to its exact application context."""

    agent_id: str
    tool_name: str
    tool_use_id: str
    arguments: FrozenMap[str, FrozenJsonValue]
    source_input: FrozenMap[str, FrozenJsonValue]
    contract_digest: str
    plan_digest: str
    configuration_digest: str
    deployment: FrozenMap[str, FrozenJsonValue]
    context: HarnessRunContext
    event_id: str

    def __post_init__(self) -> None:
        for name in ("arguments", "source_input", "deployment"):
            object.__setattr__(self, name, frozen_object(getattr(self, name)))

    @property
    def call_digest(self) -> str:
        return content_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "tool_name": self.tool_name,
            "tool_use_id": self.tool_use_id,
            "arguments": thaw(self.arguments),
            "source_input": thaw(self.source_input),
            "contract_digest": self.contract_digest,
            "plan_digest": self.plan_digest,
            "configuration_digest": self.configuration_digest,
            "deployment": thaw(self.deployment),
            "context": self.context.to_dict(),
            "event_id": self.event_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> HarnessPendingCall:
        raw = json_object("Harness state", thaw(frozen_object(value)))
        if set(raw) != {
            "agent_id",
            "tool_name",
            "tool_use_id",
            "arguments",
            "source_input",
            "contract_digest",
            "plan_digest",
            "configuration_digest",
            "deployment",
            "context",
            "event_id",
        }:
            raise ValueError("Invalid pending Harness call keys")
        return cls(
            agent_id=json_string("agent_id", raw["agent_id"]),
            tool_name=json_string("tool_name", raw["tool_name"]),
            tool_use_id=json_string("tool_use_id", raw["tool_use_id"]),
            arguments=frozen_object(raw["arguments"]),
            source_input=frozen_object(raw["source_input"]),
            contract_digest=json_string("contract_digest", raw["contract_digest"]),
            plan_digest=json_string("plan_digest", raw["plan_digest"]),
            configuration_digest=json_string("configuration_digest", raw["configuration_digest"]),
            deployment=frozen_object(raw["deployment"]),
            context=HarnessRunContext.from_dict(raw["context"]),
            event_id=json_string("event_id", raw["event_id"]),
        )


@dataclass(frozen=True)
class HarnessApproval:
    """An application decision for the exact pending-call digest."""

    call_digest: str
    approved: bool
    evidence_ref: str

    def __post_init__(self) -> None:
        if type(self.approved) is not bool or not self.evidence_ref.strip():
            raise ValueError("Approval requires an explicit decision and evidence reference")


@dataclass(frozen=True)
class HarnessCompleted:
    value: object
    request_id: str | None
    status: Literal["completed"] = "completed"


@dataclass(frozen=True)
class HarnessInterrupted:
    pending: HarnessPendingCall
    request_id: str | None
    status: Literal["interrupted"] = "interrupted"


@dataclass(frozen=True)
class HarnessFailed:
    reason: str
    request_id: str | None = None
    stop_reason: str | None = None
    response_received: bool = False
    status: Literal["failed"] = "failed"


HarnessResult = HarnessCompleted | HarnessInterrupted | HarnessFailed
