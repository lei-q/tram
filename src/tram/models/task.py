"""Task records (EVM data source) and agent task specs."""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field

from tram.models.evidence import Evidence


class TaskStatus(enum.StrEnum):
    TODO = "todo"
    DOING = "doing"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"


class TaskRecord(BaseModel):
    id: str
    title: str
    status: TaskStatus = TaskStatus.TODO
    points: float = 1.0
    est_points: float = 1.0
    spent_points: float = 0.0
    evidence: list[Evidence] = Field(default_factory=list)
    commit_refs: list[str] = Field(default_factory=list)
    rework_count: int = 0


class TaskSpec(BaseModel):
    """A unit of work handed to an external coding agent."""

    id: str
    prompt: str
    allowed_tools: list[str] = Field(default_factory=list)
    max_turns: int | None = None
    timeout_s: int = 1800
