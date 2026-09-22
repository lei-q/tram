"""Append-only event log entries - the black box."""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class EventKind(enum.StrEnum):
    PROJECT_INITIALIZED = "project_initialized"
    PHASE_CHANGED = "phase_changed"
    CHECK_COMPLETED = "check_completed"
    GATE_EVALUATED = "gate_evaluated"
    INTENT_BLOCKED = "intent_blocked"
    CR_CREATED = "cr_created"
    CR_STATUS_CHANGED = "cr_status_changed"
    AGENT_RUN_STARTED = "agent_run_started"
    AGENT_RUN_FINISHED = "agent_run_finished"
    ARTIFACT_GENERATED = "artifact_generated"
    HUMAN_DECISION = "human_decision"
    EVM_SNAPSHOT = "evm_snapshot"
    RISK_REGISTERED = "risk_registered"
    TASK_UPDATED = "task_updated"
    QA_FAILED = "qa_failed"
    QA_PASSED = "qa_passed"
    BASELINE_SAVED = "baseline_saved"
    FILE_SAVED = "file_saved"
    SESSION_MERGED = "session_merged"
    AUTOPILOT_STEP = "autopilot_step"


class TramEvent(BaseModel):
    seq: int
    ts: datetime
    kind: EventKind
    source: str
    data: dict = Field(default_factory=dict)
    refs: dict[str, str] = Field(default_factory=dict)
