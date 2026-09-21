"""KPI reports (gate/defect MTTR, rework rate) - read the black box, compute deterministically."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tram.models.events import EventKind, TramEvent
from tram.models.state import ProjectState
from tram.models.task import TaskStatus

_UNHEALTHY = ("fail", "blocked_pending_human")


@dataclass
class MTTRItem:
    subject: str  # gate id or task id
    breaches: int = 0
    total_seconds: float = 0.0

    @property
    def mttr_seconds(self) -> float:
        return round(self.total_seconds / self.breaches, 1) if self.breaches else 0.0


@dataclass
class MTTRReport:
    items: list[MTTRItem]
    overall_mttr_seconds: float
    open_subjects: list[str]  # currently red / unfixed


@dataclass
class ReworkReport:
    tasks_total: int
    tasks_done: int
    tasks_with_rework: int
    rework_events: int
    rate: float  # tasks_with_rework / tasks_done


@dataclass
class EscapeReport:
    """逃逸率：修复验证通过后又复发的缺陷 / 全部缺陷（QA 漏网之鱼）。"""

    defects_total: int  # qa_failed 次数
    defects_escaped: int  # 复发（同上游任务此前的返工已验证通过）
    rate: float


def _pair_mttr(
    events: list[TramEvent],
    open_pred: Callable[[TramEvent], bool],
    close_pred: Callable[[TramEvent], bool],
    subject_of: Callable[[TramEvent], str | None],
) -> MTTRReport:
    """通用越界-恢复配对：open 到下一次同主体 close 的时长 = 一次恢复。"""
    per: dict[str, MTTRItem] = {}
    open_since: dict[str, TramEvent] = {}
    for event in events:
        subject = subject_of(event)
        if subject is None:
            continue
        if open_pred(event):
            open_since.setdefault(subject, event)  # consecutive opens keep the first
        elif close_pred(event) and subject in open_since:
            opened = open_since.pop(subject)
            item = per.setdefault(subject, MTTRItem(subject=subject))
            item.breaches += 1
            item.total_seconds += (event.ts - opened.ts).total_seconds()
    closed = [m for m in per.values() if m.breaches]
    total_breaches = sum(m.breaches for m in closed)
    overall = (
        round(sum(m.total_seconds for m in closed) / total_breaches, 1) if total_breaches else 0.0
    )
    return MTTRReport(
        items=sorted(per.values(), key=lambda m: m.subject),
        overall_mttr_seconds=overall,
        open_subjects=sorted(open_since),
    )


def _gate_subject(event: TramEvent) -> str | None:
    return event.refs.get("gate")


def mttr_report(events: list[TramEvent]) -> MTTRReport:
    """门禁即信号：fail/blocked 到同门禁下一次 pass 的时长。"""
    return _pair_mttr(
        events,
        open_pred=lambda e: (
            e.kind == EventKind.GATE_EVALUATED and e.data.get("status") in _UNHEALTHY
        ),
        close_pred=lambda e: e.kind == EventKind.GATE_EVALUATED and e.data.get("status") == "pass",
        subject_of=_gate_subject,
    )


def _defect_subject(event: TramEvent) -> str | None:
    if event.kind == EventKind.QA_FAILED:
        return event.refs.get("rework_task") or event.data.get("rework_task")
    if event.kind == EventKind.QA_PASSED:
        return event.refs.get("task")
    return None


def defect_mttr(events: list[TramEvent]) -> MTTRReport:
    """缺陷 MTTR：qa_failed 到对应返工任务 qa_passed 的时长（按返工任务 id 配对）。"""
    return _pair_mttr(
        events,
        open_pred=lambda e: e.kind == EventKind.QA_FAILED,
        close_pred=lambda e: e.kind == EventKind.QA_PASSED,
        subject_of=_defect_subject,
    )


def rework_report(state: ProjectState) -> ReworkReport:
    """返工率 = 有返工记录的已完成任务 / 已完成任务（qa fail 自动维护计数）。"""
    done = [t for t in state.tasks if t.status == TaskStatus.DONE]
    with_rework = sum(1 for t in done if t.rework_count > 0)
    return ReworkReport(
        tasks_total=len(state.tasks),
        tasks_done=len(done),
        tasks_with_rework=with_rework,
        rework_events=sum(t.rework_count for t in state.tasks),
        rate=round(with_rework / len(done), 3) if done else 0.0,
    )


def escape_report(events: list[TramEvent], state: ProjectState) -> EscapeReport:
    """逃逸率 = 复发缺陷 / 全部缺陷。

    复发：同一上游任务此前的返工已 qa pass，之后又 qa fail —— QA 没拦住。
    """
    upstream_of = {t.id: t.rework_of for t in state.tasks if t.rework_of}
    fixed_upstreams: set[str] = set()
    defects = 0
    escaped = 0
    for event in events:
        if event.kind == EventKind.QA_PASSED:
            upstream = upstream_of.get(event.refs.get("task", ""))
            if upstream:
                fixed_upstreams.add(upstream)
        elif event.kind == EventKind.QA_FAILED:
            defects += 1
            if event.refs.get("task") in fixed_upstreams:
                escaped += 1
    return EscapeReport(
        defects_total=defects,
        defects_escaped=escaped,
        rate=round(escaped / defects, 3) if defects else 0.0,
    )
