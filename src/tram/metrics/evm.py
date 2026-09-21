"""EVM engine - deterministic, computed from TaskRecords. No LLM, ever."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tram.config import EvmThresholds
from tram.context import TramContext
from tram.models.evm import EVMSnapshot
from tram.models.risk import RiskItem, RiskStrategy
from tram.models.state import ProjectState
from tram.models.task import TaskStatus

EVM_DIR = ".tram/artifacts/evm"


def compute_snapshot(state: ProjectState, day: dt.date | None = None) -> EVMSnapshot:
    """PV = 全部已规划任务点数；EV = done 任务的估算点数；AC = 已投入点数。"""
    return EVMSnapshot(
        date=day or dt.date.today(),
        pv=sum(t.est_points for t in state.tasks),
        ev=sum(t.est_points for t in state.tasks if t.status == TaskStatus.DONE),
        ac=sum(t.spent_points for t in state.tasks),
        task_refs=[t.id for t in state.tasks],
    )


def evaluate_thresholds(snap: EVMSnapshot, thresholds: EvmThresholds) -> list[str]:
    """Return human-readable breach reasons; empty list = within bounds."""
    reasons: list[str] = []
    if snap.pv > 0:
        if snap.spi < thresholds.spi_min:
            reasons.append(f"SPI {snap.spi} 低于下限 {thresholds.spi_min}")
        elif snap.spi > thresholds.spi_max:
            reasons.append(f"SPI {snap.spi} 高于上限 {thresholds.spi_max}")
    if snap.ac > 0 and snap.cpi < thresholds.cpi_min:
        reasons.append(f"CPI {snap.cpi} 低于下限 {thresholds.cpi_min}")
    return reasons


def snapshot_dir(ctx: TramContext) -> Path:
    return ctx.repo / EVM_DIR


def save_snapshot(ctx: TramContext, snap: EVMSnapshot) -> Path:
    out = snapshot_dir(ctx)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"snapshot-{snap.date.isoformat()}.json"
    path.write_text(snap.model_dump_json(indent=2), encoding="utf-8")
    return path


def latest_snapshot(ctx: TramContext) -> EVMSnapshot | None:
    out = snapshot_dir(ctx)
    if not out.exists():
        return None
    snaps = [
        EVMSnapshot.model_validate(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(out.glob("snapshot-*.json"))
    ]
    return snaps[-1] if snaps else None


def escalate_breaches(
    snap: EVMSnapshot, reasons: list[str], trigger_seq: int | None
) -> list[RiskItem]:
    """越界即入险：每条越界原因生成一条风险（id = r-evm-<日期>-<指标>，同日重跑幂等）。"""
    day = snap.date.isoformat()
    return [
        RiskItem(
            id=f"r-evm-{day}-{'spi' if 'SPI' in reason else 'cpi'}",
            description=f"EVM 越界：{reason}",
            probability=3,
            impact=4,
            strategy=RiskStrategy.MITIGATE,
            trigger_event_seq=trigger_seq,
            owner="tram.evm",
            created_at=dt.datetime.now(dt.UTC),
        )
        for reason in reasons
    ]
