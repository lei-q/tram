"""HITL approvals - one code path for CLI and UI.

UI 审批直连事件流的形态：UI 不做第二大脑，把人的决定交给这里的
确定性函数，与 CLI 走同一条写账路径（state + 事件流）。入口不同，
source 如实记录（tram.cli.* / tram.ui），黑匣子可回放"谁在哪按的"。
"""

from __future__ import annotations

import datetime as dt

from tram.context import TramContext
from tram.cr_store import CRStore
from tram.models.cr import Approval, CRStatus, CRType
from tram.models.events import EventKind


class ApproverNotAllowedError(ValueError):
    """署名不在该 kind 的审批人名单内（干系人 lite 域）。"""


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _ensure_approver(ctx: TramContext, kind: str, by: str) -> None:
    """干系人（lite 域）：名单已配置且非空时，署名必须在名单内。"""
    allowed = ctx.config.approvers.get(kind, [])
    if allowed and by not in allowed:
        raise ApproverNotAllowedError(f"'{by}' 不在 {kind} 审批人名单内（{', '.join(allowed)}）")


def approve_baseline(
    ctx: TramContext, by: str, note: str = "", source: str = "tram.cli.baseline"
) -> int:
    """HITL: approve the scope baseline (enables G0). Returns baseline version."""
    _ensure_approver(ctx, "baseline", by)
    state = ctx.load_state()
    baseline = ctx.load_baseline()
    baseline.approved_by = by
    baseline.approved_at = _utcnow()
    baseline.dump(ctx.baseline_file)
    state.human_approvals.append(
        Approval(
            decision="approved",
            by=by,
            at=_utcnow(),
            kind="scope_baseline",
            artifact_ref=f"scope-baseline@v{baseline.version}",
            note=note,
        )
    )
    state.baselines["scope"] = f"scope-baseline@v{baseline.version}"
    ctx.state_store.save(state)
    ctx.events.append(
        EventKind.HUMAN_DECISION,
        source=source,
        data={"decision": "approved", "kind": "scope_baseline", "by": by, "note": note},
    )
    return baseline.version


def approve_release(
    ctx: TramContext, by: str, note: str = "", source: str = "tram.cli.approve"
) -> None:
    """HITL: approve the release (unblocks g3_closing_gate -> 终点站)."""
    _ensure_approver(ctx, "release", by)
    state = ctx.load_state()
    state.human_approvals.append(
        Approval(
            decision="approved",
            by=by,
            at=_utcnow(),
            kind="release",
            artifact_ref="release",
            note=note,
        )
    )
    ctx.state_store.save(state)
    ctx.events.append(
        EventKind.HUMAN_DECISION,
        source=source,
        data={"decision": "approved", "kind": "release", "by": by, "note": note},
    )


def decide_cr(
    ctx: TramContext,
    cr_id: str,
    decision: str,
    by: str,
    note: str = "",
    source: str = "tram.cli.cr",
) -> CRStatus:
    """HITL: approve/reject a change request. Returns the resulting status."""
    _ensure_approver(ctx, "cr", by)
    store = CRStore(ctx.crs_dir)
    cr = store.load(cr_id)
    if cr is None:
        raise ValueError(f"unknown CR: {cr_id}")
    state = ctx.load_state()

    cr.approvals.append(
        Approval(
            decision=decision,
            by=by,
            at=_utcnow(),
            kind="cr",
            artifact_ref=cr.id,
            note=note,
        )
    )
    cr.status = CRStatus.REJECTED if decision == "rejected" else CRStatus.APPROVED
    store.save(cr)

    if decision == "approved" and cr.type in (CRType.SCOPE, CRType.PROCUREMENT):
        # 变更即分支：批准的范围/采购变更直接进入基线（版本 +1），并视为已实施
        baseline = ctx.load_baseline()
        baseline.allowed_paths.extend(p for p in cr.impact.changed_paths)
        baseline.version += 1
        baseline.dump(ctx.baseline_file)
        cr.status = CRStatus.IMPLEMENTED
        store.save(cr)
        state.baselines["scope"] = f"scope-baseline@v{baseline.version}"

    if cr.id in state.open_crs and cr.status not in (
        CRStatus.DRAFT,
        CRStatus.ANALYZING,
        CRStatus.AWAITING_HUMAN,
        CRStatus.APPROVED,
    ):
        state.open_crs.remove(cr.id)
    ctx.state_store.save(state)
    ctx.events.append(
        EventKind.CR_STATUS_CHANGED,
        source=source,
        data={"cr": cr.id, "status": cr.status.value, "by": by, "note": note},
        refs={"cr": cr.id},
    )
    ctx.events.append(
        EventKind.HUMAN_DECISION,
        source=source,
        data={"decision": decision, "kind": "cr", "cr": cr.id, "by": by, "note": note},
        refs={"cr": cr.id},
    )
    return cr.status
