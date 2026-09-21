"""AgentRunner protocol - Tram drives engines, never the other way around."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from tram.models.task import TaskSpec


class RunnerUnavailableError(RuntimeError):
    pass


class RunResult(BaseModel):
    task_id: str
    runner: str
    status: str  # ok / error / blocked
    summary: str = ""
    tool_trace: list[dict] = Field(default_factory=list)
    worktree: str | None = None
    worktree_branch: str | None = None


class AgentRunner(Protocol):
    name: str

    def run(self, task: TaskSpec, workspace: Path) -> RunResult: ...
