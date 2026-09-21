"""EVM engine: deterministic math from TaskRecords + threshold breach escalation."""

from __future__ import annotations

import json
from datetime import date

from typer.testing import CliRunner

from tram.cli import app
from tram.config import EvmThresholds, TramConfig
from tram.metrics.evm import (
    compute_snapshot,
    escalate_breaches,
    evaluate_thresholds,
    latest_snapshot,
    save_snapshot,
)
from tram.models.events import EventKind
from tram.models.evm import EVMSnapshot
from tram.models.state import ProjectState
from tram.models.task import TaskRecord, TaskStatus

runner = CliRunner()


def _state() -> ProjectState:
    state = ProjectState(project_name="demo")
    state.tasks.append(
        TaskRecord(
            id="T-001", title="done task", status=TaskStatus.DONE, est_points=8, spent_points=9
        )
    )
    state.tasks.append(
        TaskRecord(id="T-002", title="planned task", status=TaskStatus.TODO, est_points=2)
    )
    return state


def test_compute_snapshot_math():
    snap = compute_snapshot(_state(), day=date(2026, 9, 21))
    assert (snap.pv, snap.ev, snap.ac) == (10, 8, 9)
    assert snap.spi == 0.8
    assert snap.cpi == 0.889
    assert snap.task_refs == ["T-001", "T-002"]


def test_threshold_evaluation():
    t = EvmThresholds()
    snap = compute_snapshot(_state(), day=date(2026, 9, 21))
    assert "SPI" in evaluate_thresholds(snap, t)[0]
    assert "CPI" in evaluate_thresholds(snap, t)[1]

    healthy = compute_snapshot(
        ProjectState(
            project_name="x",
            tasks=[
                TaskRecord(
                    id="T-1", title="a", status=TaskStatus.DONE, est_points=5, spent_points=5
                )
            ],
        ),
        day=date(2026, 9, 21),
    )
    assert evaluate_thresholds(healthy, t) == []

    # SPI above the upper bound also breaches (gold-plating / over-reporting)
    fast = EVMSnapshot(date=date(2026, 9, 21), pv=4, ev=5, ac=5)
    reasons = evaluate_thresholds(fast, t)
    assert len(reasons) == 1 and "高于上限" in reasons[0]

    # empty project: nothing to evaluate
    empty = compute_snapshot(ProjectState(project_name="x"), day=date(2026, 9, 21))
    assert evaluate_thresholds(empty, t) == []


def test_snapshot_save_and_latest_roundtrip(ctx):
    snap = compute_snapshot(_state(), day=date(2026, 9, 20))
    save_snapshot(ctx, snap)
    newer = compute_snapshot(_state(), day=date(2026, 9, 21))
    path = save_snapshot(ctx, newer)
    assert path.name == "snapshot-2026-09-21.json"
    assert latest_snapshot(ctx) == newer
    # snapshots reload as full EVMSnapshot (computed fields included)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["spi"] == 0.8


def test_escalation_ids_are_deterministic_and_per_metric():
    snap = compute_snapshot(_state(), day=date(2026, 9, 21))
    reasons = evaluate_thresholds(snap, EvmThresholds())
    risks = escalate_breaches(snap, reasons, trigger_seq=42)
    assert [r.id for r in risks] == ["r-evm-2026-09-21-spi", "r-evm-2026-09-21-cpi"]
    assert all(r.trigger_event_seq == 42 for r in risks)
    assert all(r.owner == "tram.evm" for r in risks)
    # same day + same metrics -> same ids (idempotent merge upstream)
    again = escalate_breaches(snap, reasons, trigger_seq=43)
    assert [r.id for r in again] == [r.id for r in risks]


def test_config_thresholds_roundtrip(tmp_path):
    cfg = TramConfig(project_name="x", evm_thresholds=EvmThresholds(spi_min=0.5, cpi_min=0.4))
    path = tmp_path / "tram.yaml"
    cfg.dump(path)
    loaded = TramConfig.load(path)
    assert loaded.evm_thresholds.spi_min == 0.5
    assert loaded.evm_thresholds.cpi_min == 0.4
    assert loaded.evm_thresholds.spi_max == 1.15


def test_cli_task_points_and_evm_snapshot_registers_risks(ctx, monkeypatch):
    monkeypatch.chdir(ctx.repo)
    state = ctx.load_state()
    state.tasks.append(
        TaskRecord(id="T-001", title="a", status=TaskStatus.DONE, est_points=4, spent_points=5)
    )
    state.tasks.append(TaskRecord(id="T-002", title="b", status=TaskStatus.BLOCKED, est_points=4))
    ctx.state_store.save(state)

    result = runner.invoke(app, ["task", "points", "T-002", "--est", "4"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["task", "list"])
    assert result.exit_code == 0
    assert "T-002" in result.output

    result = runner.invoke(app, ["evm", "snapshot"])
    assert result.exit_code == 0, result.output
    assert "SPI 0.5 低于下限 0.85" in result.output
    assert "CPI 0.8 低于下限 0.9" in result.output
    assert result.output.count("risk registered") == 2

    state = ctx.load_state()
    today = date.today().isoformat()
    assert {r.id for r in state.risks} == {f"r-evm-{today}-spi", f"r-evm-{today}-cpi"}

    events = [e.kind for e in ctx.events.read()]
    assert events.count(EventKind.EVM_SNAPSHOT) == 1
    assert events.count(EventKind.RISK_REGISTERED) == 2
    assert EventKind.TASK_UPDATED in events

    # re-running the same day must not duplicate risks
    result = runner.invoke(app, ["evm", "snapshot"])
    assert result.exit_code == 0, result.output
    assert "risk registered" not in result.output
    state = ctx.load_state()
    assert len(state.risks) == 2

    result = runner.invoke(app, ["evm", "show"])
    assert result.exit_code == 0
    assert "低于下限" in result.output


def test_cli_task_points_errors(ctx, monkeypatch):
    monkeypatch.chdir(ctx.repo)
    result = runner.invoke(app, ["task", "points", "T-999", "--est", "1"])
    assert result.exit_code == 1
    assert "unknown task" in result.output
    state = ctx.load_state()
    state.tasks.append(TaskRecord(id="T-001", title="a"))
    ctx.state_store.save(state)
    result = runner.invoke(app, ["task", "points", "T-001"])
    assert result.exit_code == 1
    assert "nothing to update" in result.output


def test_cli_evm_show_before_any_snapshot(ctx, monkeypatch):
    monkeypatch.chdir(ctx.repo)
    result = runner.invoke(app, ["evm", "show"])
    assert result.exit_code == 0
    assert "no snapshots yet" in result.output
