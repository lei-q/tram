"""Shared action services - CLI verbs and UI buttons dispatch the same code.

产品纲领：CLI 是基础能力，友好直观的 UI 交互才是立命之根本。UI 全功能
不等于绕过治理：这里的每个动词都是 CLI 同款确定性服务（写 state、写
事件流、过 Intent Guard），UI 只是长了按钮的同一条铁轨。
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import yaml

from tram.context import TramContext
from tram.cr_store import CRStore
from tram.governance.domains import classify_path
from tram.governance.gate_runner import GateRunner
from tram.governance.intent_guard import IntentGuard, classify_violations
from tram.metrics.evm import (
    compute_snapshot,
    escalate_breaches,
    evaluate_thresholds,
    save_snapshot,
)
from tram.models.events import EventKind
from tram.models.gates import GateResult
from tram.models.task import TaskRecord, TaskStatus


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def next_task_id(state) -> str:
    """分配不冲突的任务 id：以现有 id 为准，防止计数器与手工编辑漂移。"""
    nums = [int(m.group(1)) for t in state.tasks if (m := re.fullmatch(r"T-(\d+)", t.id))]
    seq = max(state.next_task_seq, max(nums, default=0) + 1)
    state.next_task_seq = seq + 1
    return f"T-{seq:03d}"


# ---------- 治理主线 ----------


def gate_run(ctx: TramContext, gate_id: str) -> GateResult:
    """跑单座门禁：确定性检查 + 策略决策（含整改轮次计数）。"""
    return GateRunner(ctx).run(gate_id)


def run_flow(ctx: TramContext) -> tuple[dict, str]:
    """沿主线连续过门禁（LangGraph 或零依赖解释器）。"""
    from tram.orchestration import invoke_flow

    return invoke_flow(GateRunner(ctx))


def next_lap(ctx: TramContext, by: str) -> dict:
    """环线折返：收尾站二选一——G3 终局收车，或本动词进入下一圈.

    过程组每圈重复一遍（渐进明细的机械落点）：带着上一圈完整的
    需求/风险/变更/EVM 账回到规划站；WBS 与基线在 G1 重新校对。
    """
    from tram.models.events import EventKind
    from tram.models.state import Phase

    state = ctx.load_state()
    if state.phase not in (Phase.CLOSING, Phase.DONE):
        raise ValueError(
            f"环线折返要在收尾站（或终点站再出发）发车（当前 {state.phase.value}）；"
            "终局收车走 G3 release"
        )
    state.iteration += 1
    state.phase = Phase.PLANNING
    ctx.state_store.save(state)
    ctx.events.append(
        EventKind.PHASE_CHANGED,
        source="tram.lap",
        data={
            "from": "closing",
            "to": "planning",
            "iteration": state.iteration,
            "lap": True,
            "by": by,
        },
    )
    return {"iteration": state.iteration, "phase": state.phase.value}


def monitor_sweep(ctx: TramContext) -> dict:
    """巡检：监控不是一座车站，是随车乘务——EVM（当天幂等）+ Guard + 风险概览 + 需求对账."""
    from tram.metrics.evm import latest_snapshot

    state = ctx.load_state()
    out: dict = {"date": _utcnow().date().isoformat(), "iteration": state.iteration}

    latest = latest_snapshot(ctx)
    if latest is not None and latest.date.isoformat() == out["date"]:
        out["evm"] = {"skipped": True, "detail": "今天已快照", "spi": latest.spi, "cpi": latest.cpi}
    else:
        snap, _path, reasons, new_risks = evm_snapshot(ctx)
        out["evm"] = {
            "skipped": False,
            "spi": snap.spi,
            "cpi": snap.cpi,
            "breaches": reasons,
            "new_risks": [r.id for r in new_risks],
        }

    decision, cr = guard_check(ctx, None)
    out["guard"] = {
        "ok": decision.ok,
        "violations": decision.violations,
        "cr": cr.id if cr else None,
    }

    open_risks = [r for r in ctx.load_state().risks if r.status != "closed"]
    out["risks"] = {"open": len(open_risks)}
    out["requirements"] = requirement_gaps(ctx)
    return out


# ---------- 风险闭环 / 需求对账（监控贯穿、渐进明细的机械落点） ----------


def register_guard_risk(
    ctx: TramContext, cr_id: str, violations: list[str], trigger_seq: int
) -> None:
    """越界拦截入风险登记册（P×I=9 风暴级）——账本之外，风险面板也要看得见。"""
    from tram.models.risk import RiskItem, RiskStrategy

    state = ctx.load_state()
    rid = f"r-guard-{cr_id}"
    if any(r.id == rid for r in state.risks):
        return  # 同一 CR 只入一次
    state.risks.append(
        RiskItem(
            id=rid,
            description=f"越界改动被拦截（CR {cr_id}）：{'、'.join(violations[:5])}",
            probability=3,
            impact=3,
            strategy=RiskStrategy.MITIGATE,
            trigger_event_seq=trigger_seq,
            owner="tram.guard",
        )
    )
    ctx.state_store.save(state)


def risk_resolve(ctx: TramContext, risk_id: str, status: str, by: str) -> dict:
    """风险状态流转：open/watching/closed——人裁，事件留痕。"""
    from tram.models.events import EventKind
    from tram.models.risk import RiskStatus

    try:
        target = RiskStatus(status)
    except ValueError as exc:
        raise ValueError(f"unknown status: {status}（open | watching | closed）") from exc
    state = ctx.load_state()
    item = next((r for r in state.risks if r.id == risk_id), None)
    if item is None:
        raise ValueError(f"unknown risk: {risk_id}（见风险气象台 / tram kpi）")
    item.status = target
    ctx.state_store.save(state)
    ctx.events.append(
        EventKind.RISK_RESOLVED,
        source="tram.risk",
        data={"risk": risk_id, "status": target.value, "by": by},
        refs={"risk": risk_id},
    )
    return {"risk": risk_id, "status": target.value}


def requirement_gaps(ctx: TramContext) -> dict:
    """需求对账（渐进明细）：项目定义了 WBS 工作包时，还有多少在途任务没锚定。"""
    packages = ctx.load_wbs()
    if not packages:
        return {"unanchored": 0, "mode": "free"}
    state = ctx.load_state()
    unanchored = [t.id for t in state.tasks if not t.wbs_package and t.status != "done"]
    return {"unanchored": len(unanchored), "tasks": unanchored[:20], "mode": "anchored"}


def guard_check(ctx: TramContext, paths: list[str] | None = None) -> tuple:
    """Intent Guard：越界即门红 + 自动建 CR（依赖清单越界按 procurement）。"""
    baseline = ctx.load_baseline()
    paths = list(paths) if paths else ctx.git.pending_changes()
    decision = IntentGuard(baseline).check(paths)
    cr = None
    if not decision.ok:
        event = ctx.events.append(
            EventKind.INTENT_BLOCKED,
            source="tram.guard",
            data={"violations": decision.violations},
        )
        state = ctx.load_state()
        cr = CRStore(ctx.crs_dir).create_draft(
            state,
            classify_violations(decision.violations),
            changed_paths=decision.violations,
            reason="intent guard: changes outside scope baseline",
            trigger_event_seq=event.seq,
        )
        ctx.state_store.save(state)
        ctx.events.append(
            EventKind.CR_CREATED,
            source="tram.guard",
            data={"cr": cr.id, "type": cr.type.value, "paths": decision.violations},
            refs={"event": str(event.seq)},
        )
        register_guard_risk(ctx, cr.id, decision.violations, event.seq)
    return decision, cr


# ---------- 任务 / QA 闭环 ----------


def task_points(
    ctx: TramContext, task_id: str, est: float | None = None, spent: float | None = None
) -> TaskRecord:
    """设置任务点数（EVM 数据源）。"""
    state = ctx.load_state()
    record = state.task(task_id)
    if record is None:
        raise ValueError(f"unknown task: {task_id}")
    if est is None and spent is None:
        raise ValueError("nothing to update: pass est and/or spent")
    if est is not None:
        record.est_points = est
    if spent is not None:
        record.spent_points = spent
    ctx.state_store.save(state)
    ctx.events.append(
        EventKind.TASK_UPDATED,
        source="tram.task",
        data={
            "task": task_id,
            "est_points": record.est_points,
            "spent_points": record.spent_points,
            "status": record.status.value,
        },
        refs={"task": task_id},
    )
    return record


def qa_fail(ctx: TramContext, task_id: str, note: str = "", by: str = "qa") -> TaskRecord:
    """QA 复现失败：登记缺陷 + 自动创建返工任务（rework_of 链）。"""
    state = ctx.load_state()
    record = state.task(task_id)
    if record is None:
        raise ValueError(f"unknown task: {task_id}")
    record.rework_count += 1
    rework = TaskRecord(
        id=next_task_id(state),
        title=f"修复 {task_id}: {note.splitlines()[0][:60] if note else 'rework'}",
        status=TaskStatus.TODO,
        est_points=record.est_points,
        rework_of=task_id,
    )
    state.tasks.append(rework)
    ctx.state_store.save(state)
    ctx.events.append(
        EventKind.QA_FAILED,
        source="tram.qa",
        data={"task": task_id, "rework_task": rework.id, "note": note, "by": by},
        refs={"task": task_id, "rework_task": rework.id},
    )
    return rework


def qa_pass(ctx: TramContext, task_id: str, note: str = "", by: str = "qa") -> TaskRecord:
    """QA 验证通过：返工闭环（缺陷 MTTR 的终点）。"""
    state = ctx.load_state()
    record = state.task(task_id)
    if record is None:
        raise ValueError(f"unknown task: {task_id}")
    if record.status != TaskStatus.DONE:
        record.status = TaskStatus.DONE
        record.spent_points = record.est_points
    ctx.state_store.save(state)
    ctx.events.append(
        EventKind.QA_PASSED,
        source="tram.qa",
        data={"task": task_id, "note": note, "by": by},
        refs={"task": task_id},
    )
    return record


# ---------- 工件 / 度量 ----------


def artifact_generate(ctx: TramContext, kinds: list[str]) -> list:
    """生成带证据链的治理工件（确定性渲染）。"""
    from tram.artifacts.generator import ARTIFACT_KINDS, ArtifactGenerator

    unknown = [k for k in kinds if k not in ARTIFACT_KINDS]
    if unknown:
        raise ValueError(f"unknown kinds: {unknown}; known: {', '.join(ARTIFACT_KINDS)}")
    if not kinds:
        raise ValueError("nothing to generate: pass kinds or --all")
    generator = ArtifactGenerator(ctx)
    return [generator.generate(kind) for kind in kinds]


def evm_snapshot(ctx: TramContext, day: str | None = None) -> tuple:
    """EVM 快照 + 越界自动入险。返回 (snap, path, reasons, new_risks)。"""
    snap_day = dt.date.fromisoformat(day) if day else None
    state = ctx.load_state()
    snap = compute_snapshot(state, snap_day)
    path = save_snapshot(ctx, snap)
    event = ctx.events.append(
        EventKind.EVM_SNAPSHOT, source="tram.evm", data=snap.model_dump(mode="json")
    )
    reasons = evaluate_thresholds(snap, ctx.config.evm_thresholds)
    new_risks: list = []
    if reasons:
        existing = {r.id for r in state.risks}
        for risk in escalate_breaches(snap, reasons, trigger_seq=event.seq):
            if risk.id not in existing:
                state.risks.append(risk)
                new_risks.append(risk)
        if new_risks:
            ctx.state_store.save(state)
            for risk in new_risks:
                ctx.events.append(
                    EventKind.RISK_REGISTERED,
                    source="tram.evm",
                    data={"risk": risk.id, "description": risk.description},
                    refs={"risk": risk.id, "event": str(event.seq)},
                )
    return snap, path, reasons, new_risks


# ---------- 文件车厢（UI 文件管理的服务层落点） ----------

MAX_READ_BYTES = 512 * 1024


class ProtectedPath(ValueError):
    """结构保护路径（.tram 证据 / .git 历史）：谁都不能经此改写。"""


class GuardBlocked(Exception):
    """写文件越界：携带 Guard 决定与自动立案的 CR（与 `tram guard check` 同款）。"""

    def __init__(self, decision, cr) -> None:
        self.decision = decision
        self.cr = cr
        detail = f"changes outside scope baseline: {', '.join(decision.violations)}"
        if cr:
            detail += f"（CR {cr.id} 已自动立案）"
        super().__init__(detail)


def _safe_target(repo: Path, rel: str) -> Path:
    """把相对路径钉死在 repo 根内：拒绝绝对路径、.. 与越界 symlink。"""
    candidate = (repo / rel).resolve()
    if not candidate.is_relative_to(repo.resolve()):
        raise ValueError(f"path escapes repo root: {rel}")
    return candidate


def file_list(ctx: TramContext, rel_dir: str = "") -> list[dict]:
    """列目录（目录优先、.git 不展示），并标注 pending changes 与知识域归属。"""
    root = _safe_target(ctx.repo, rel_dir or ".")
    if not root.is_dir():
        raise ValueError(f"not a directory: {rel_dir}")
    repo_root = ctx.repo.resolve()
    changed = set(ctx.git.pending_changes())
    items: list[dict] = []
    for entry in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
        if entry.is_dir() and entry.name == ".git":
            continue  # git 内部目录不是项目文件，不进车厢
        rel = entry.relative_to(repo_root).as_posix()
        item: dict = {
            "name": entry.name,
            "type": "dir" if entry.is_dir() else "file",
            "path": rel,
            "changed": rel in changed,
        }
        if entry.is_file():
            item["size"] = entry.stat().st_size
            item["domain"] = classify_path(rel)
        items.append(item)
    return items


def file_flat(ctx: TramContext, limit: int = 2000) -> list[dict]:
    """全仓平铺文件清单（按知识域分组的视图用）——只列文件，跳过 .git/.tram worktrees。"""
    repo_root = ctx.repo.resolve()
    changed = set(ctx.git.pending_changes())
    items: list[dict] = []
    for path in sorted(repo_root.rglob("*")):
        if len(items) >= limit:
            break
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(repo_root).as_posix()
        parts = rel.split("/")
        if ".git" in parts:
            continue
        if parts[0] == ".tram" and len(parts) > 1 and parts[1] == "worktrees":
            continue  # 会话车厢的常驻 worktree 不是项目文件
        items.append(
            {
                "name": path.name,
                "type": "file",
                "path": rel,
                "changed": rel in changed,
                "size": path.stat().st_size,
                "domain": classify_path(rel),
            }
        )
    return items


def file_read(ctx: TramContext, rel: str) -> dict:
    """读文件：文本内容 + 该路径的基线归属（UI 提前亮信号灯）。"""
    target = _safe_target(ctx.repo, rel)
    if not target.is_file():
        raise ValueError(f"not a file: {rel}")
    raw = target.read_bytes()
    decision = IntentGuard(ctx.load_baseline()).check([Path(rel).as_posix()])
    return {
        "path": Path(rel).as_posix(),
        "size": len(raw),
        "binary": b"\0" in raw[:8192],
        "truncated": len(raw) > MAX_READ_BYTES,
        "content": None
        if b"\0" in raw[:8192]
        else raw[:MAX_READ_BYTES].decode("utf-8", errors="replace"),
        "in_baseline": decision.ok,
        "violations": decision.violations,
    }


def file_save(ctx: TramContext, rel: str, content: str, by: str) -> dict:
    """UI 的人工编辑与 agent 修改同一条 Guard 铁轨：越界拦截并自动立案。"""
    posix = Path(rel).as_posix()
    top = posix.split("/", 1)[0]
    if top in {".tram", ".git"}:
        raise ProtectedPath(f"{top}/ 受结构保护（事件证据 / git 历史），不经文件车厢改写")
    target = _safe_target(ctx.repo, rel)
    decision, cr = guard_check(ctx, [posix])
    if not decision.ok:
        raise GuardBlocked(decision, cr)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    target.write_bytes(data)
    ctx.events.append(
        EventKind.FILE_SAVED,
        source="tram.ui",
        data={"path": posix, "bytes": len(data), "by": by},
        refs={"path": posix},
    )
    return {"path": posix, "bytes": len(data)}


# ---------- 基线（UI 直接编辑的落点） ----------


def baseline_save(ctx: TramContext, yaml_text: str, by: str) -> int:
    """保存编辑后的范围基线（先 schema 校验再落盘；版本不变，批准走 HITL）。"""
    from tram.cr_store import ScopeBaseline

    data = yaml.safe_load(yaml_text)
    baseline = ScopeBaseline.model_validate(data)
    baseline.dump(ctx.baseline_file)
    ctx.events.append(
        EventKind.BASELINE_SAVED,
        source="tram.baseline",
        data={"by": by, "version": baseline.version, "allowed": baseline.allowed_paths},
    )
    return baseline.version
