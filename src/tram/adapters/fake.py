"""FakeRunner - offline, deterministic agent for tests and demos.

stream() / run() 消费同一事件流（与 claude 适配器同构）：会话车厢
测试不需要真引擎，也能走完整的流式轨道。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tram.adapters.base import RunResult, fold_stream
from tram.models.task import TaskSpec


class FakeRunner:
    name = "fake"

    def __init__(self, writes: dict[str, str] | None = None) -> None:
        self.writes = writes or {}

    def stream(self, task: TaskSpec, workspace: Path) -> Iterator[dict[str, Any]]:
        # 与真引擎同构：没有句柄就现场生成一个（claude 的 session 同款语义）
        sid = task.session_id or f"sess-{uuid.uuid4().hex[:8]}"
        yield {"type": "system", "subtype": "init", "session_id": sid}
        for path, content in self.writes.items():
            target = workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            yield {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": "write", "input": {"path": path}}]
                },
            }
        yield {
            "type": "result",
            "subtype": "success",
            "session_id": sid,
            "result": f"wrote {len(self.writes)} file(s)",
        }

    def run(self, task: TaskSpec, workspace: Path) -> RunResult:
        result = fold_stream(self.stream(task, workspace), self.name)
        result.task_id = task.id
        return result
