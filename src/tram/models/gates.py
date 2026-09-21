"""Gate / check specs and results. Deterministic first: no LLM in here."""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class CheckType(enum.StrEnum):
    COMMAND = "command"
    COVERAGE = "coverage"
    EVIDENCE = "evidence"


class CheckSpec(BaseModel):
    id: str
    type: CheckType
    cmd: list[str] | None = None
    expect: dict = Field(default_factory=dict)  # e.g. {"exit_code": 0}
    min_percent: float | None = None
    scope: list[str] = Field(default_factory=list)
    require: str | None = None  # evidence requirement id
    artifact: str | None = None  # artifact kind for require=artifact_present
    timeout_s: int = 600


class CheckResult(BaseModel):
    id: str
    status: str  # pass / fail / error / skipped
    output: str = ""
    duration_ms: int = 0


class GateSpec(BaseModel):
    blocking: bool = True
    remediation_limit: int = 3
    checks: list[CheckSpec] = Field(default_factory=list)


class GateStatus(enum.StrEnum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED_PENDING_HUMAN = "blocked_pending_human"


class GateResult(BaseModel):
    gate_id: str
    ts: datetime
    status: GateStatus
    checks: list[CheckResult] = Field(default_factory=list)
    decision_reasons: list[str] = Field(default_factory=list)
    needs_human: bool = False
    remediation_round: int = 0
