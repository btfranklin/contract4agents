"""One compiled contract joined to one reviewed materialization plan."""

from __future__ import annotations

from dataclasses import dataclass

from contract4agents.compiler import CompilerArtifacts, artifact_digests
from contract4agents.ir import CanonicalIR
from contract4agents.planning._models import MaterializationPlan


@dataclass(frozen=True)
class PlannedSystem:
    """A provider-neutral contract and the plan for one target and profile."""

    artifacts: CompilerArtifacts
    plan: MaterializationPlan

    def __post_init__(self) -> None:
        if self.plan.contract_digest != self.artifacts.contract_digest:
            raise ValueError("Materialization plan must use the compiled contract digest")
        if self.plan.artifact_digests != artifact_digests(self.artifacts):
            raise ValueError("Materialization plan must use the compiled artifact digests")

    @property
    def ir(self) -> CanonicalIR:
        """Return the canonical contract used to make this plan."""

        return self.artifacts.ir


__all__ = ["PlannedSystem"]
