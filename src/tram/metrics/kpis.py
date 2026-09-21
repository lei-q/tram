"""KPI reports (MTTR / rework) - read the black box, compute deterministically."""

from __future__ import annotations

from dataclasses import dataclass

from tram.models.events import EventKind, TramEvent
from tram.models.state import ProjectState
from tram.models.task import TaskStatus

_UNHEALTHY = ("fail", "blocked_pending_human")


@dataclass
class GateMTTR:
    gate_id: str
    breaches: int = 0  # fail/blocked episodes closed by a later pass
    total_seconds: float = 0.0

    @property
    def mttr_seconds(self) -> float:
        return round(self.total_seconds / self.breaches, 1) if self.breaches else 0.0


@dataclass
class MTTRReport:
    gates: list[GateMTTR]
    overall_mttr_seconds: float
    open_breaches: list[str]  # gates currently red with no recovery yet


@dataclass
class ReworkReport:
    tasks_total: int
    tasks_done: int
    tasks_with_rework: int
    rework_events: int
    rate: float  # tasks_with_rework / tasks_done


def mttr_report(events: list[TramEvent]) -> MTTRReport:
    """门禁即信号：一次 fail/blocked 到同门禁下一次 pass 的时长 = 一次越界恢复。"""
    per: dict[str, GateMTTR] = {}
    open_since: dict[str, TramEvent] = {}
    for event in events:
        if event.kind != EventKind.GATE_EVALUATED:
            continue
        gate_id = event.refs.get("gate", "?")
        status = event.data.get("status")
        if status in _UNHEALTHY:
            open_since.setdefault(gate_id, event)
        elif status == "pass" and gate_id in open_since:
            opened = open_since.pop(gate_id)
            m = per.setdefault(gate_id, GateMTTR(gate_id=gate_id))
            m.breaches += 1
            m.total_seconds += (event.ts - opened.ts).total_seconds()
    closed = [m for m in per.values() if m.breaches]
    total_breaches = sum(m.breaches for m in closed)
    overall = (
        round(sum(m.total_seconds for m in closed) / total_breaches, 1) if total_breaches else 0.0
    )
    return MTTRReport(
        gates=sorted(per.values(), key=lambda m: m.gate_id),
        overall_mttr_seconds=overall,
        open_breaches=sorted(open_since),
    )


def rework_report(state: ProjectState) -> ReworkReport:
    """返工率 = 有返工记录的已完成任务 / 已完成任务（QA 闭环接入后由闭环维护计数）。"""
    done = [t for t in state.tasks if t.status == TaskStatus.DONE]
    with_rework = sum(1 for t in done if t.rework_count > 0)
    return ReworkReport(
        tasks_total=len(state.tasks),
        tasks_done=len(done),
        tasks_with_rework=with_rework,
        rework_events=sum(t.rework_count for t in state.tasks),
        rate=round(with_rework / len(done), 3) if done else 0.0,
    )
