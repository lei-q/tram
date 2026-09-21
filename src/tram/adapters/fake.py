"""FakeRunner - offline, deterministic agent for tests and demos."""

from __future__ import annotations

from pathlib import Path

from tram.adapters.base import RunResult
from tram.models.task import TaskSpec


class FakeRunner:
    name = "fake"

    def __init__(self, writes: dict[str, str] | None = None) -> None:
        self.writes = writes or {}

    def run(self, task: TaskSpec, workspace: Path) -> RunResult:
        trace = []
        for path, content in self.writes.items():
            target = workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            trace.append({"type": "fake_write", "path": path})
        return RunResult(
            task_id=task.id,
            runner=self.name,
            status="ok",
            summary=f"wrote {len(self.writes)} file(s)",
            tool_trace=trace,
        )
