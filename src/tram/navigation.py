"""领航员 - 只读护航建议：不驾驶列车，只给驾驶员开行动清单.

护航者定位（2026-09-23 纲领）的兑现：原「自动驾驶」会替司机开票、
孵整改会话、并线——那是抢方向盘。领航员**只读**：扫状态/工件/CR/
风险/EVM/需求对账，按确定性清单吐出一组「建议动作」（对应调度台
既有的按钮/ verbs），执行永远是驾驶员的手。零副作用：不跑门禁、
不写状态、不发事件——看完即弃，审计在驾驶员点下去之后才开始。
"""

from __future__ import annotations

from tram.context import TramContext
from tram.models.risk import RiskStatus
from tram.models.task import TaskStatus

G1_KINDS = ("wbs", "schedule", "quality_plan", "risk_register")


def navigate(ctx: TramContext) -> dict:
    """扫一遍护航仪表，返回有序建议清单。只读，永不执行。"""
    state = ctx.load_state()
    recommendations: list[dict] = []

    if not any(
        a.kind == "scope_baseline" and a.decision == "approved" for a in state.human_approvals
    ):
        recommendations.append(
            {
                "action": "approve-baseline",
                "label": "站台审批批准范围基线（G0 放行前提）",
                "where": "站台审批 / tram baseline approve",
            }
        )

    if state.open_crs:
        recommendations.append(
            {
                "action": "cr",
                "label": f"裁决 {len(state.open_crs)} 条未决 CR（批准扩基线 / 拒绝关闭）",
                "where": "站台审批 / tram cr sync",
            }
        )

    kinds = _artifact_kinds(ctx)
    missing_g1 = [k for k in G1_KINDS if k not in kinds]
    if "charter" not in kinds:
        recommendations.append(
            {
                "action": "artifact.generate",
                "label": "开票补项目章程（G0 检查项）",
                "where": "🎫 开票",
            }
        )
    if missing_g1:
        recommendations.append(
            {
                "action": "artifact.generate",
                "label": f"开票补规划工件：{', '.join(missing_g1)}（G1 检查项）",
                "where": "🎫 开票",
            }
        )

    if not state.tasks:
        recommendations.append(
            {
                "action": "chat",
                "label": "任务册是空的——去会话车厢发第一条开工消息（自动立任务）",
                "where": "会话车厢 · 主驾席",
            }
        )

    import datetime as dt

    from tram import operations
    from tram.metrics.evm import latest_snapshot

    today = dt.date.today().isoformat()
    snap = latest_snapshot(ctx)
    if snap is None or snap.date.isoformat() != today:
        recommendations.append(
            {
                "action": "monitor.sweep",
                "label": "今天的巡检还没跑（EVM 快照 + Guard + 对账）",
                "where": "👁 巡检",
            }
        )

    storm = [
        r for r in state.risks if r.status != RiskStatus.CLOSED and r.probability * r.impact >= 9
    ]
    if storm:
        recommendations.append(
            {
                "action": "risk.resolve",
                "label": f"{len(storm)} 项风暴级风险未决（P×I≥9），需要裁决或降级",
                "where": "风险气象台 / tram risk resolve",
            }
        )

    gaps = operations.requirement_gaps(ctx)
    if gaps.get("mode") == "anchored" and gaps.get("unanchored"):
        recommendations.append(
            {
                "action": "anchor",
                "label": f"{gaps['unanchored']} 个在途任务未锚定 WBS 工作包（渐进明细没跟上）",
                "where": "会话车厢开锚定会话消化需求",
            }
        )

    doing = sum(1 for t in state.tasks if t.status == TaskStatus.DOING)
    if not recommendations:
        if state.phase.value == "closing":
            recommendations.extend(
                [
                    {
                        "action": "approve-release",
                        "label": "收尾站二选一：G3 release 终局放行",
                        "where": "站台审批",
                    },
                    {
                        "action": "lap.next",
                        "label": "或 ↺ 环线下一圈（带账回规划站再迭代）",
                        "where": "↺ 下一圈",
                    },
                ]
            )
        else:
            recommendations.append(
                {
                    "action": "flow.run",
                    "label": "护航仪表全绿——可以试探性推进（绿灯走红灯停）",
                    "where": "▶ 全线运行",
                }
            )

    return {
        "phase": state.phase.value,
        "iteration": state.iteration,
        "doing_tasks": doing,
        "recommendations": recommendations,
        "summary": f"{len(recommendations)} 条建议" if recommendations else "全绿",
    }


def _artifact_kinds(ctx: TramContext) -> set[str]:
    import json

    index = ctx.artifacts_index
    if not index.exists():
        return set()
    try:
        items = json.loads(index.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return {str(item.get("kind")) for item in items if isinstance(item, dict)}
