"""自动驾驶 - 决策表哑司机：锚点之间自动推进，锚点处硬停.

架构位次（与铁路 ATO 同构）：
- 联锁（确定性，永不让渡）：门禁裁决 / Intent Guard / CR / HITL 锚点
- 司机（本模块）：确定性决策表，从「合法动作集」里选下一个动作——
  读状态、调既有 operations/chat 服务、写事件，自己不发明任何判定
- 执行：operations 动词（gate/artifact/evm）+ ChatService 整改会话

四个永不自动的锚点：基线批准（G0）、CR 裁决、release 放行（G3）、
整改升级待人审。自动驾驶只并线**自己孵化**的整改会话（并线本身仍走
merge_session 的 Guard 铁轨），人的会话永不自动碰。

护栏：干跑模式（探门看信号可以——事件照留痕，但零动作：不开票、
不孵会话、不并线）；急停文件 `.tram/autopilot-stop`（存在即停，删掉
恢复）；步数预算（默认 10，防打转）。
"""

from __future__ import annotations

from tram import operations
from tram.chat import ChatService
from tram.context import TramContext
from tram.models.events import EventKind

STOP_FILE = ".tram/autopilot-stop"
DEFAULT_MAX_STEPS = 10


def route_reasons(reasons: list[str]) -> str:
    """门禁红灯原因 -> 动作（确定性路由表）。anchor=锚点硬停。"""
    text = " ".join(reasons)
    if "awaiting human approval" in text or "open scope change request" in text:
        return "anchor"
    if (
        "missing artifacts" in text
        or "charter_present" in text
        or "artifacts_verified" in text
        or "evidence_ok" in text
    ):
        return "artifacts"  # 证据缺失先补工件；反复不行会被步数预算接住
    if "failed checks" in text:
        return "rework"  # 命令类检查失败（tests/coverage/…）都是要修的活
    return "unknown"


class Autopilot:
    """决策表司机。run() 一趟到底：绿则推、红则路由、锚点停。"""

    def __init__(
        self,
        ctx: TramContext,
        engine: str = "fake",
        dry_run: bool = False,
        max_steps: int = DEFAULT_MAX_STEPS,
        by: str = "autopilot",
    ) -> None:
        self.ctx = ctx
        self.engine = engine
        self.dry_run = dry_run
        self.max_steps = max_steps
        self.by = by
        self.journey: list[str] = []
        self.actions = 0

    def run(self) -> dict:
        for _ in range(self.max_steps):
            if (self.ctx.repo / STOP_FILE).exists():
                return self._stop(f"手动急停（{STOP_FILE} 存在；删除后可再跑）")
            state = self.ctx.load_state()
            if state.open_crs:
                return self._stop(f"锚点：{len(state.open_crs)} 条 CR 待人裁决（站台审批）")
            if not any(
                a.kind == "scope_baseline" and a.decision == "approved"
                for a in state.human_approvals
            ):
                return self._stop("锚点：范围基线待人批准（站台审批 / tram baseline approve）")

            flow, _engine = operations.run_flow(self.ctx)
            self.journey.extend(f"🚋 {line}" for line in flow.get("journey", []))
            stop = flow.get("stop_reason")
            if not stop or "终点站" in stop:
                return self._stop("全线绿灯，抵达终点站 🎉")
            self.journey.append(f"🛑 {stop}")

            evaluated = (flow.get("evaluated") or [{}])[-1]
            reasons = evaluated.get("reasons", [])
            action = route_reasons(reasons)
            if action == "anchor":
                return self._stop(f"锚点：{'；'.join(reasons)}")
            if action == "unknown":
                return self._stop(f"未知红灯原因，人工接管：{'；'.join(reasons)}")
            if self.dry_run:
                return self._stop(f"[干跑] 下一步将执行：{action}（{'; '.join(reasons)}）")
            if action == "artifacts":
                self._do_artifacts()
                continue
            outcome = self._do_rework(reasons)
            if outcome is not None:
                return self._stop(outcome)
        return self._stop(f"步数预算用尽（{self.max_steps}）——查 journey 看卡在哪，勿盲目重跑")

    # ---------- 动作 ----------

    def _do_artifacts(self) -> None:
        arts = operations.artifact_generate(
            self.ctx,
            ["charter", "wbs", "schedule", "quality_plan", "risk_register", "scope_baseline"],
        )
        self.actions += 1
        names = ", ".join(a.kind for a in arts)
        self.journey.append(f"🎫 司机动作：开票（{names}）")
        self.ctx.events.append(
            EventKind.AUTOPILOT_STEP,
            source="tram.autopilot",
            data={"action": "artifact.generate", "kinds": names},
        )

    def _do_rework(self, reasons: list[str]) -> str | None:
        """孵一个整改会话修 G2 红灯，修完走正门并线。返回 None 继续，否则停车原因。"""
        chat = ChatService(self.ctx, inline=True)
        prompt = (
            "G2 质量门红灯，请整改以下问题（只改测试相关的最小范围，跑通后停下）：\n"
            + "\n".join(f"- {r}" for r in reasons)
        )
        session, job = chat.send_message(None, prompt, by=self.by, engine=self.engine)
        self.journey.append(f"🔧 司机动作：整改会话 {session['id']}（engine={self.engine}）")
        if job.status != "ok":
            return f"整改会话未产出（{job.status}: {job.error or job.cr}），人工接管"
        merged = chat.merge_session(session["id"], by=self.by)
        if not merged.get("merged"):
            return f"整改产出未并线（{merged.get('reason')}：{merged.get('detail')}），人工接管"
        self.actions += 1
        self.journey.append(
            f"🔀 司机动作：整改已并线（{(merged.get('commit') or '')[:8]} "
            f"· {len(merged.get('paths', []))} 路径）"
        )
        self.ctx.events.append(
            EventKind.AUTOPILOT_STEP,
            source="tram.autopilot",
            data={
                "action": "rework.merge",
                "session": session["id"],
                "commit": merged.get("commit"),
            },
        )
        return None

    def _stop(self, reason: str) -> dict:
        self.journey.append(f"🏁 {reason}")
        return {
            "mode": "dry-run" if self.dry_run else "live",
            "engine": self.engine,
            "journey": self.journey,
            "actions": self.actions,
            "stop": reason,
        }
