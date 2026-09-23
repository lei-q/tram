"""ProjectState - the single structured source of truth agents collaborate on."""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field

from tram.models.cr import Approval, ChangeRequest
from tram.models.gates import GateResult
from tram.models.risk import RiskItem
from tram.models.task import TaskRecord


class Phase(enum.StrEnum):
    INITIATING = "initiating"
    PLANNING = "planning"
    EXECUTING = "executing"
    MONITORING = "monitoring"
    CLOSING = "closing"
    DONE = "done"


class ProjectState(BaseModel):
    project_name: str
    phase: Phase = Phase.INITIATING
    iteration: int = 1  # 环线第几圈——过程组每圈重复一遍，不是走一遍的直线
    baselines: dict[str, str] = Field(default_factory=dict)  # name -> version ref
    tasks: list[TaskRecord] = Field(default_factory=list)
    open_crs: list[str] = Field(default_factory=list)  # CR ids
    risks: list[RiskItem] = Field(default_factory=list)
    gate_history: list[GateResult] = Field(default_factory=list)
    human_approvals: list[Approval] = Field(default_factory=list)
    next_task_seq: int = 1
    next_cr_seq: int = 1
    remediation_rounds: dict[str, int] = Field(default_factory=dict)

    def task(self, task_id: str) -> TaskRecord | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def is_cr_open(self, cr_id: str) -> bool:
        return cr_id in self.open_crs

    def open_change_requests(self, all_crs: list[ChangeRequest]) -> list[ChangeRequest]:
        by_id = {cr.id: cr for cr in all_crs}
        return [by_id[i] for i in self.open_crs if i in by_id]
