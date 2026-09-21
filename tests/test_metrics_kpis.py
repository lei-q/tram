"""KPI reports: MTTR from the black box, rework from task records."""

from __future__ import annotations

import datetime as dt
import json

from typer.testing import CliRunner

from tram.cli import app
from tram.metrics.kpis import mttr_report, rework_report
from tram.models.events import EventKind, TramEvent
from tram.models.state import ProjectState
from tram.models.task import TaskRecord, TaskStatus

runner = CliRunner()


def _gate_ev(seq: int, ts: dt.datetime, status: str, gate: str = "g2_quality_gate") -> TramEvent:
    return TramEvent(
        seq=seq,
        ts=ts,
        kind=EventKind.GATE_EVALUATED,
        source="gate_runner",
        data={"status": status},
        refs={"gate": gate},
    )


def _t(minutes_after_10: int) -> dt.datetime:
    return dt.datetime(2026, 9, 21, 10, 0, tzinfo=dt.UTC) + dt.timedelta(minutes=minutes_after_10)


def test_mttr_pairs_fail_with_next_pass():
    events = [
        _gate_ev(1, _t(0), "fail"),
        _gate_ev(2, _t(30), "fail"),  # consecutive fails do not open a second episode
        _gate_ev(3, _t(60), "pass"),
    ]
    report = mttr_report(events)
    assert report.open_breaches == []
    assert len(report.gates) == 1
    m = report.gates[0]
    assert (m.gate_id, m.breaches, m.mttr_seconds) == ("g2_quality_gate", 1, 3600.0)
    assert report.overall_mttr_seconds == 3600.0


def test_mttr_blocked_pending_human_counts_as_breach():
    events = [_gate_ev(1, _t(0), "blocked_pending_human"), _gate_ev(2, _t(30), "pass")]
    report = mttr_report(events)
    assert report.gates[0].mttr_seconds == 1800.0


def test_mttr_multiple_episodes_and_gates():
    events = [
        _gate_ev(1, _t(0), "fail", gate="g1"),
        _gate_ev(2, _t(10), "pass", gate="g1"),
        _gate_ev(3, _t(20), "fail", gate="g1"),
        _gate_ev(4, _t(50), "pass", gate="g1"),
        _gate_ev(5, _t(5), "fail", gate="g2"),
        _gate_ev(6, _t(65), "pass", gate="g2"),
    ]
    report = mttr_report(events)
    by_gate = {m.gate_id: m for m in report.gates}
    assert by_gate["g1"].breaches == 2
    assert by_gate["g1"].mttr_seconds == round((600 + 1800) / 2, 1)
    assert by_gate["g2"].mttr_seconds == 3600.0
    assert report.overall_mttr_seconds == round((600 + 1800 + 3600) / 3, 1)


def test_mttr_open_breach_has_no_recovery_yet():
    events = [
        _gate_ev(1, _t(0), "pass"),
        _gate_ev(2, _t(10), "fail"),
    ]
    report = mttr_report(events)
    assert report.open_breaches == ["g2_quality_gate"]
    assert report.gates == []
    assert report.overall_mttr_seconds == 0.0


def test_mttr_ignores_other_events():
    noise = TramEvent(
        seq=1, ts=_t(0), kind=EventKind.HUMAN_DECISION, source="tram.cli", data={"status": "fail"}
    )
    assert mttr_report([noise]).gates == []


def test_rework_report():
    state = ProjectState(
        project_name="demo",
        tasks=[
            TaskRecord(id="T-1", title="a", status=TaskStatus.DONE, rework_count=2),
            TaskRecord(id="T-2", title="b", status=TaskStatus.DONE),
            TaskRecord(id="T-3", title="c", status=TaskStatus.BLOCKED, rework_count=1),
        ],
    )
    report = rework_report(state)
    assert (report.tasks_total, report.tasks_done) == (3, 2)
    assert report.tasks_with_rework == 1
    assert report.rate == 0.5
    assert rework_report(ProjectState(project_name="x")).rate == 0.0


def test_cli_kpi_from_backdated_jsonl(ctx, monkeypatch):
    monkeypatch.chdir(ctx.repo)
    # hand-write the black box: a g2 breach at 10:00 recovered at 11:00 (MTTR 1h),
    # plus a live task with spent points so the rework line renders
    events_path = ctx.events.path
    lines = [
        {
            "seq": 1,
            "ts": "2026-09-21T10:00:00+00:00",
            "kind": "gate_evaluated",
            "source": "gate_runner",
            "data": {"status": "fail"},
            "refs": {"gate": "g2_quality_gate"},
        },
        {
            "seq": 2,
            "ts": "2026-09-21T11:00:00+00:00",
            "kind": "gate_evaluated",
            "source": "gate_runner",
            "data": {"status": "pass"},
            "refs": {"gate": "g2_quality_gate"},
        },
    ]
    with events_path.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")

    state = ctx.load_state()
    state.tasks.append(
        TaskRecord(id="T-001", title="a", status=TaskStatus.DONE, est_points=4, spent_points=5)
    )
    ctx.state_store.save(state)

    result = runner.invoke(app, ["kpi"], env={"COLUMNS": "220"})
    assert result.exit_code == 0, result.output
    assert "3600.0s (1 breach(es))" in result.output
    assert "rework rate" in result.output
    assert "0.0%" in result.output
