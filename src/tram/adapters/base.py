"""AgentRunner protocol - Tram drives engines, never the other way around."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Protocol

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
    session_id: str | None = None  # 引擎侧会话句柄（claude session 等）


class AgentRunner(Protocol):
    name: str

    def run(self, task: TaskSpec, workspace: Path) -> RunResult: ...


class StreamingRunner(AgentRunner, Protocol):
    """能边跑边吐引擎事件的 runner（会话车厢的流式轨道）。

    stream() 逐个 yield 引擎原始事件 dict；run() 消费同一事件流，
    所以实现流式即同时获得阻塞语义，不会两套行为。
    """

    def stream(self, task: TaskSpec, workspace: Path) -> Iterator[dict[str, Any]]: ...


def fold_stream(events: Iterable[dict[str, Any]], runner: str) -> RunResult:
    """stream-json 风格事件流 -> RunResult（assistant.tool_use → 轨迹，result → 终态）。

    各适配器的 stream()/run() 共用这套折叠语义，保证流式与阻塞结果一致。
    """
    trace: list[dict] = []
    final: dict = {}
    for obj in events:
        kind = obj.get("type")
        if kind == "assistant":
            for block in obj.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    trace.append(
                        {"type": "tool_use", "name": block.get("name"), "input": block.get("input")}
                    )
        elif kind == "result":
            final = obj
    status = "ok" if final.get("subtype") == "success" else "error"
    return RunResult(
        task_id="",  # filled by run()
        runner=runner,
        status=status,
        summary=str(final.get("result") or final.get("subtype") or "no result event")[:2000],
        tool_trace=trace,
        session_id=final.get("session_id"),
    )
