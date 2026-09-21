"""Artifacts - lightweight project files that must carry evidence links."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tram.models.evidence import Evidence

# Artifact kinds the planning gate (G1) requires.
G1_REQUIRED_KINDS = ("wbs", "schedule", "quality_plan", "risk_register")


class Artifact(BaseModel):
    id: str
    kind: str  # charter / wbs / schedule / quality_plan / risk_register / adr / status_report ...
    path: str
    evidence: list[Evidence] = Field(default_factory=list)
    generated_by: str = "tram"
    gate_id: str | None = None

    @property
    def verified(self) -> bool:
        return bool(self.evidence) and all(e.verified for e in self.evidence)

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence)
