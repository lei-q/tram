"""Change requests - baseline changes must go through CR + impact + approval."""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class CRType(enum.StrEnum):
    SCOPE = "scope"
    ARCHITECTURE = "architecture"
    SCHEDULE = "schedule"
    COST = "cost"
    MAJOR_COST = "major_cost"
    RELEASE = "release"


class CRStatus(enum.StrEnum):
    DRAFT = "draft"
    ANALYZING = "analyzing"
    AWAITING_HUMAN = "awaiting_human"
    APPROVED = "approved"
    REJECTED = "rejected"
    IMPLEMENTED = "implemented"


class ImpactAnalysis(BaseModel):
    changed_paths: list[str] = Field(default_factory=list)
    affected_tasks: list[str] = Field(default_factory=list)
    notes: str = ""


class Approval(BaseModel):
    decision: str  # approved / rejected
    by: str
    at: datetime
    kind: str  # scope_baseline / release / cr / ...
    artifact_ref: str | None = None
    note: str = ""


class PRRef(BaseModel):
    """D5 PR review 流：CR 裁决可接 GitHub PR review（approved/rejected）。"""

    repo: str  # owner/name
    number: int


class ChangeRequest(BaseModel):
    id: str
    type: CRType
    status: CRStatus = CRStatus.DRAFT
    title: str = ""
    impact: ImpactAnalysis = Field(default_factory=ImpactAnalysis)
    approvals: list[Approval] = Field(default_factory=list)
    trigger_event_seq: int | None = None
    reason: str = ""
    created_at: datetime | None = None
    pr: PRRef | None = None
