"""UI API: state / artifacts / events + SSE live tail + 调度台动作派发 + HITL 审批.

产品纲领：CLI 是基础能力，UI 交互才是立命之根本。默认只读；`tram ui --approve`
开启写模式后，POST /api/action（调度台）与 POST /api/approve（站台审批）都走与
CLI 完全相同的确定性服务层（tram.operations / governance.approvals）——写 state、
写事件流、过 Intent Guard，一个按钮都不在铁轨外。写模式带双 CSRF 防护
（会话令牌 + Origin 校验）。
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections import defaultdict
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tram import operations
from tram.artifacts.generator import ARTIFACT_KINDS
from tram.chat import ChatService, MergeRefused
from tram.context import TramContext, load_context
from tram.cr_store import CRStore
from tram.governance import approvals
from tram.governance.domains import domains_payload
from tram.governance.gate_runner import load_gate_specs
from tram.metrics.evm import evaluate_thresholds, latest_snapshot
from tram.metrics.kpis import MTTRReport, defect_mttr, escape_report, mttr_report, rework_report
from tram.models.artifacts import Artifact
from tram.models.events import TramEvent
from tram.models.gates import GateResult
from tram.models.risk import RiskStatus
from tram.models.task import TaskStatus

STATIC_DIR = Path(__file__).parent / "static"
POLL_SECONDS = 1.0
REFRESH_KINDS = {
    "phase_changed",
    "gate_evaluated",
    "cr_status_changed",
    "cr_created",
    "agent_run_finished",
    "artifact_generated",
    "human_decision",
    "evm_snapshot",
    "risk_registered",
    "task_updated",
    "qa_failed",
    "qa_passed",
    "baseline_saved",
    "session_merged",
    "autopilot_step",
    "risk_resolved",
    "knowledge_distilled",
}


def _mttr_json(report: MTTRReport) -> dict:
    return {
        "overall_seconds": report.overall_mttr_seconds,
        "open_subjects": report.open_subjects,
        "items": [
            {"subject": m.subject, "breaches": m.breaches, "mttr_seconds": m.mttr_seconds}
            for m in report.items
        ],
    }


class ApprovalRequest(BaseModel):
    """站台审批请求：UI 把署名的人工决定 POST 进事件流。"""

    action: Literal["baseline", "release", "cr_approve", "cr_reject"]
    id: str = ""
    by: str
    note: str = ""


class ActionRequest(BaseModel):
    """调度台动作请求：动词 + 参数，派发到与 CLI 同一条 operations 服务层。"""

    verb: str
    args: dict = Field(default_factory=dict)
    by: str = ""  # qa.fail / qa.pass / baseline.save 需要署名


class FileSaveRequest(BaseModel):
    """文件车厢保存请求：UI 的人工编辑，与 agent 修改过同一道 Guard。"""

    path: str
    content: str
    by: str


class ChatOpenRequest(BaseModel):
    """开一条引擎会话：engine（claude|openhands|fake）+ 司机署名 + WBS 工作包锚定。"""

    engine: str = "claude"
    by: str
    wbs_package: str | None = None


class ChatSendRequest(BaseModel):
    """会话消息：session 为空即新建会话（engine 仅此时生效）。"""

    session: str | None = None
    engine: str | None = None
    message: str
    by: str
    wbs_package: str | None = None


def create_app(
    repo: Path | None = None, allow_approvals: bool = False, inline_jobs: bool = False
) -> FastAPI:
    ctx: TramContext = load_context(repo)
    approval_token = secrets.token_urlsafe(24) if allow_approvals else ""
    chat = ChatService(ctx, inline=inline_jobs)  # 会话车厢：jobs + SSE 流式
    app = FastAPI(title="tram-ui", docs_url=None, redoc_url=None)

    def snapshot() -> dict:
        state = ctx.load_state()
        tasks = state.tasks
        by_gate: dict[str, GateResult] = {}
        for result in state.gate_history:
            by_gate[result.gate_id] = result  # history is append-ordered
        gates_file = ctx.gates_file if ctx.gates_file.exists() else None
        specs = load_gate_specs(gates_file or ctx.packaged_policies / "gates.yaml")
        crs = CRStore(ctx.crs_dir).open_crs()
        snap = latest_snapshot(ctx)
        events = list(ctx.events.read())
        rework = rework_report(state)
        escape = escape_report(events, state)
        return {
            "project_name": state.project_name,
            "phase": state.phase.value,
            "iteration": state.iteration,
            "sandbox": ctx.config.sandbox.value,
            "policy_engine": ctx.config.policy_engine.value,
            "events_count": ctx.events.count(),
            "evm": (
                {
                    "date": snap.date.isoformat(),
                    "pv": snap.pv,
                    "ev": snap.ev,
                    "ac": snap.ac,
                    "spi": snap.spi,
                    "cpi": snap.cpi,
                    "breaches": evaluate_thresholds(snap, ctx.config.evm_thresholds),
                }
                if snap
                else None
            ),
            "scope_approved": any(
                a.kind == "scope_baseline" and a.decision == "approved"
                for a in state.human_approvals
            ),
            "baseline_version": int(
                state.baselines.get("scope", "scope-baseline@v1").rsplit("v", 1)[-1]
            ),
            "tasks": {
                "total": len(tasks),
                "done": sum(t.status == TaskStatus.DONE for t in tasks),
                "doing": sum(t.status == TaskStatus.DOING for t in tasks),
                "blocked": sum(t.status == TaskStatus.BLOCKED for t in tasks),
                "points_total": sum(t.est_points for t in tasks),
                "points_done": sum(t.est_points for t in tasks if t.status == TaskStatus.DONE),
            },
            "task_list": [
                {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status.value,
                    "est": t.est_points,
                    "spent": t.spent_points,
                    "rework": t.rework_count,
                    "rework_of": t.rework_of,
                }
                for t in tasks
            ],
            "open_crs": [
                {
                    "id": cr.id,
                    "type": cr.type.value,
                    "status": cr.status.value,
                    "paths": cr.impact.changed_paths,
                    "pr": cr.pr.number if cr.pr else None,
                }
                for cr in crs
            ],
            "gate_status": {
                gate_id: (by_gate[gate_id].status.value if gate_id in by_gate else "idle")
                for gate_id in specs
            },
            "last_gate": (
                {
                    "id": state.gate_history[-1].gate_id,
                    "status": state.gate_history[-1].status.value,
                }
                if state.gate_history
                else None
            ),
            "risks": [
                {
                    "id": r.id,
                    "description": r.description,
                    "probability": r.probability,
                    "impact": r.impact,
                    "strategy": r.strategy.value,
                    "owner": r.owner,
                    "status": r.status.value,
                    "trigger_event_seq": r.trigger_event_seq,
                }
                for r in state.risks
                if r.status != RiskStatus.CLOSED
            ],
            "storm_risks": sum(
                1
                for r in state.risks
                if r.status != RiskStatus.CLOSED and r.probability * r.impact >= 9
            ),
            "requirement_gaps": operations.requirement_gaps(ctx),
            "kpi": {
                "gate_mttr": _mttr_json(mttr_report(events)),
                "defect_mttr": _mttr_json(defect_mttr(events)),
                "rework": {
                    "rate": rework.rate,
                    "tasks_with_rework": rework.tasks_with_rework,
                    "tasks_done": rework.tasks_done,
                },
                "escape": {
                    "rate": escape.rate,
                    "defects_escaped": escape.defects_escaped,
                    "defects_total": escape.defects_total,
                },
            },
        }

    @app.get("/api/state")
    def api_state() -> dict:
        return snapshot()

    @app.get("/api/artifacts")
    def api_artifacts() -> list[dict]:
        if not ctx.artifacts_index.exists():
            return []
        items = [
            Artifact.model_validate(obj)
            for obj in json.loads(ctx.artifacts_index.read_text(encoding="utf-8"))
        ]
        return [
            {
                "kind": a.kind,
                "path": a.path,
                "generated_by": a.generated_by,
                "evidence_total": len(a.evidence),
                "evidence_verified": sum(1 for e in a.evidence if e.verified),
                "verified": a.verified,
                "has_evidence": a.has_evidence,
            }
            for a in items
        ]

    @app.get("/api/events")
    def api_events(limit: int = 100, kind: str | None = None) -> list[dict]:
        events = list(ctx.events.read(kind=kind))
        return [e.model_dump(mode="json") for e in events[-limit:]]

    @app.get("/api/ui-config")
    def api_ui_config() -> dict:
        # 同源可读（无 CORS 头，跨域 JS 读不到响应），令牌只发给本页
        return {"approvals_enabled": allow_approvals, "token": approval_token}

    def _require_write(request: Request) -> None:
        # 写模式三道闸：功能开关、会话令牌、同源 Origin（CSRF）
        if not allow_approvals:
            raise HTTPException(403, "只读 UI（默认）——`tram ui --approve` 才开启写模式")
        supplied = request.headers.get("x-tram-token", "")
        if supplied != approval_token or not approval_token:
            raise HTTPException(403, "bad write token")
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host", ""):
            raise HTTPException(403, f"cross-origin write rejected: {origin}")

    # 调度台动词 → operations 服务。每个动词都是 CLI 同款确定性入口，
    # 这里只做派发与 JSON 化，绝不自带第二套逻辑。
    def _apply_action(verb: str, args: dict, by: str) -> dict:
        if (
            verb in {"qa.fail", "qa.pass", "baseline.save", "lap.next", "risk.resolve"}
            and not by.strip()
        ):
            raise HTTPException(422, f"{verb} 要署名：by 不能为空")
        try:
            if verb == "gate.run":
                result = operations.gate_run(ctx, str(args.get("gate", "")))
                return {
                    "gate": result.gate_id,
                    "status": result.status.value,
                    "needs_human": result.needs_human,
                    "reasons": result.decision_reasons,
                    "checks": [
                        {"id": c.id, "status": c.status, "output": c.output[:200]}
                        for c in result.checks
                    ],
                }
            if verb == "flow.run":
                final, engine = operations.run_flow(ctx)
                return {
                    "engine": engine,
                    "stop_reason": final.get("stop_reason"),
                    "journey": final.get("journey", []),
                }
            if verb == "guard.check":
                decision, cr = operations.guard_check(ctx, args.get("paths") or None)
                return {
                    "ok": decision.ok,
                    "violations": decision.violations,
                    "cr": cr.id if cr else None,
                    "cr_type": cr.type.value if cr else None,
                }
            if verb == "task.points":
                record = operations.task_points(
                    ctx, str(args.get("task", "")), args.get("est"), args.get("spent")
                )
                return {"task": record.id, "est": record.est_points, "spent": record.spent_points}
            if verb == "qa.fail":
                rework = operations.qa_fail(
                    ctx, str(args.get("task", "")), str(args.get("note", "")), by
                )
                return {"task": args.get("task"), "rework_task": rework.id}
            if verb == "qa.pass":
                record = operations.qa_pass(
                    ctx, str(args.get("task", "")), str(args.get("note", "")), by
                )
                return {"task": record.id, "status": record.status.value}
            if verb == "artifact.generate":
                kinds = list(ARTIFACT_KINDS) if args.get("all") else list(args.get("kinds") or [])
                arts = operations.artifact_generate(ctx, kinds)
                return {"artifacts": [{"kind": a.kind, "path": str(a.path)} for a in arts]}
            if verb == "evm.snapshot":
                snap, path, reasons, risks = operations.evm_snapshot(ctx, args.get("day"))
                return {
                    "date": snap.date.isoformat(),
                    "spi": snap.spi,
                    "cpi": snap.cpi,
                    "reasons": reasons,
                    "new_risks": [r.id for r in risks],
                    "path": str(path),
                }
            if verb == "baseline.save":
                version = operations.baseline_save(ctx, str(args.get("yaml", "")), by)
                return {"version": version}
            if verb == "lap.next":
                return operations.next_lap(ctx, by)
            if verb == "monitor.sweep":
                return operations.monitor_sweep(ctx)
            if verb == "risk.resolve":
                return operations.risk_resolve(
                    ctx, str(args.get("risk", "")), str(args.get("status", "")), by
                )
            if verb == "knowledge.distill":
                from tram import knowledge as kb_mod

                out = kb_mod.distill(ctx, str(args.get("engine") or "claude"), by)
                if out.get("skipped"):
                    raise HTTPException(409, f"引擎不可用：{out['skipped']}")
                return out
            raise HTTPException(400, f"unknown verb: {verb}")
        except HTTPException:
            raise
        except approvals.ApproverNotAllowedError as exc:  # 干系人名单拒绝
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:  # unknown task/gate/kind 等
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, str(exc)) from exc

    @app.post("/api/action")
    def api_action(req: ActionRequest, request: Request) -> dict:
        _require_write(request)
        return _apply_action(req.verb, req.args, req.by)

    @app.get("/api/baseline")
    def api_baseline() -> dict:
        baseline = ctx.load_baseline()
        text = ctx.baseline_file.read_text(encoding="utf-8")
        return {"yaml": text, "version": baseline.version, "allowed": baseline.allowed_paths}

    # ---------- 文件车厢：列/读开放（只读默认即安全），写走 Guard ----------

    @app.get("/api/files")
    def api_files(path: str = "", flat: int = 0) -> dict:
        try:
            entries = operations.file_flat(ctx) if flat else operations.file_list(ctx, path)
            return {"dir": path, "entries": entries}
        except ValueError as exc:  # 越界 / 非目录
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/domains")
    def api_domains() -> dict:
        """十大知识域词表 + 各过程组子过程（线路图/文件归属共用）。"""
        return domains_payload()

    @app.get("/api/wbs")
    def api_wbs() -> dict:
        """WBS 工作包（.tram/wbs.yaml，PM 规划工件）——会话锚定用。"""
        try:
            return {"packages": ctx.load_wbs()}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/file")
    def api_file(path: str) -> dict:
        try:
            return operations.file_read(ctx, path)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/file")
    def api_file_save(req: FileSaveRequest, request: Request) -> dict:
        _require_write(request)
        if not req.by.strip():
            raise HTTPException(422, "保存文件要署名：by 不能为空")
        try:
            saved = operations.file_save(ctx, req.path, req.content, req.by)
        except operations.ProtectedPath as exc:  # .tram / .git 结构保护
            raise HTTPException(403, str(exc)) from exc
        except operations.GuardBlocked as exc:  # 越界：拦截 + 已自动立案
            detail = f"越界写入已拦截：{exc}——站台审批放行 CR 或改基线后再保存"
            raise HTTPException(403, detail) from exc
        except ValueError as exc:  # 路径逃逸等
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, **saved}

    @app.get("/api/navigate")
    def api_navigate() -> dict:
        """领航员：只读护航建议（不执行任何动作——执行永远是驾驶员的手）."""
        from tram.navigation import navigate as run_navigator

        return run_navigator(ctx)

    # ---------- 会话车厢：与代码生成引擎直接对话（jobs + SSE 流式） ----------

    @app.get("/api/chat/sessions")
    def api_chat_sessions(include_closed: bool = False) -> list[dict]:
        return chat.list_sessions(include_closed)

    @app.post("/api/chat/sessions")
    def api_chat_open(req: ChatOpenRequest, request: Request) -> dict:
        _require_write(request)
        if not req.by.strip():
            raise HTTPException(422, "开会话要署名：by 不能为空")
        try:
            return chat.open_session(req.engine, req.by, wbs_package=req.wbs_package)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/chat/sessions/{sid}/close")
    def api_chat_close(sid: str, request: Request) -> dict:
        _require_write(request)
        try:
            return chat.close_session(sid)
        except ValueError as exc:
            status = 404 if "unknown" in str(exc) else 400
            raise HTTPException(status, str(exc)) from exc

    @app.post("/api/chat/sessions/{sid}/merge")
    def api_chat_merge(sid: str, req: ChatOpenRequest, request: Request) -> dict:
        """并线：会话分支经 Guard 预检合回主线（沙箱产出 → 项目文件）。"""
        _require_write(request)
        if not req.by.strip():
            raise HTTPException(422, "并线要署名：by 不能为空")
        try:
            return chat.merge_session(sid, req.by)
        except ValueError as exc:
            status = 404 if "unknown" in str(exc) else 400
            raise HTTPException(status, str(exc)) from exc
        except MergeRefused as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/chat/sessions/{sid}/log")
    def api_chat_log(sid: str) -> dict:
        """聊天历史（刷新页面后据此还原会话车厢）。"""
        if chat.get_session(sid) is None:
            raise HTTPException(404, f"unknown session: {sid}")
        return {"session": sid, "lines": chat.read_log(sid)}

    @app.delete("/api/chat/sessions/{sid}/log")
    def api_chat_log_clear(sid: str, req: ChatOpenRequest, request: Request) -> dict:
        """手动清空聊天历史（唯一的清除途径——历史默认永远保留）。"""
        _require_write(request)
        if chat.get_session(sid) is None:
            raise HTTPException(404, f"unknown session: {sid}")
        if not req.by.strip():
            raise HTTPException(422, "清空历史要署名：by 不能为空")
        return {"session": sid, "cleared": chat.clear_log(sid)}

    @app.post("/api/chat/send")
    def api_chat_send(req: ChatSendRequest, request: Request) -> dict:
        """发一条消息：引擎在常驻 worktree 里跑，Guard 铁轨收尾（见 tram.chat）。"""
        _require_write(request)
        if not req.by.strip():
            raise HTTPException(422, "发消息要署名：by 不能为空")
        try:
            session, job = chat.send_message(
                req.session, req.message, req.by, engine=req.engine, wbs_package=req.wbs_package
            )
        except ValueError as exc:
            status = 404 if "unknown session" in str(exc) else 400
            raise HTTPException(status, str(exc)) from exc
        return {"session": session, "job": chat.job_snapshot(job.id)}

    @app.post("/api/chat/jobs/{job_id}/stop")
    def api_chat_job_stop(job_id: str, request: Request) -> dict:
        """司机急停：终止本轮引擎进程——本轮作废，不进 Guard、不沉淀知识。"""
        _require_write(request)
        try:
            return chat.stop_job(job_id)
        except ValueError as exc:
            status = 404 if "unknown" in str(exc) else 409
            raise HTTPException(status, str(exc)) from exc

    @app.get("/api/chat/jobs/{job_id}")
    def api_chat_job(job_id: str, after: int = 0) -> dict:
        try:
            return chat.job_snapshot(job_id, after)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/chat/stream")
    async def api_chat_stream(job: str) -> StreamingResponse:
        """SSE：实时推 job 的增量行 + 终态快照（会话车厢的行车记录仪）。"""

        async def events():
            after = 0
            while True:
                try:
                    snap = chat.job_snapshot(job, after)
                except ValueError:
                    yield _sse_obj({"k": "error", "detail": "unknown job"})
                    return
                after = snap["next"]
                for line in snap["lines"]:
                    yield _sse_obj({"k": "line", "line": line})
                if snap["status"] != "running":
                    snap.pop("lines", None)
                    yield _sse_obj({"k": "done", "job": snap})
                    return
                await asyncio.sleep(POLL_SECONDS / 2)

        return StreamingResponse(events(), media_type="text/event-stream")

    def _sse_obj(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    @app.post("/api/approve")
    def api_approve(req: ApprovalRequest, request: Request) -> dict:
        _require_write(request)
        if not req.by.strip():
            raise HTTPException(422, "审批要署名：by 不能为空")
        source = "tram.ui"
        try:
            if req.action == "baseline":
                version = approvals.approve_baseline(ctx, req.by, req.note, source=source)
                return {"ok": True, "detail": f"scope baseline approved ✅（v{version}）"}
            if req.action == "release":
                approvals.approve_release(ctx, req.by, req.note, source=source)
                return {"ok": True, "detail": "release approved ✅ — `tram run` 可以开到终点站了"}
            # cr_approve / cr_reject
            if not req.id.strip():
                raise HTTPException(422, "CR 审批需要 id")
            decision = "approved" if req.action == "cr_approve" else "rejected"
            status = approvals.decide_cr(ctx, req.id, decision, req.by, req.note, source=source)
            return {"ok": True, "detail": f"CR {req.id} -> {status.value} ✅"}
        except HTTPException:
            raise
        except approvals.ApproverNotAllowedError as exc:  # 干系人名单拒绝
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:  # unknown CR 等
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, str(exc)) from exc

    @app.get("/api/stream")
    def api_stream() -> StreamingResponse:
        return StreamingResponse(
            tail_event_file(ctx.events.path),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


async def tail_event_file(path: Path):
    """Replay existing events, then tail the JSONL as it grows (never ends)."""
    if path.exists():
        data = path.read_bytes()
        pos = len(data)
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                yield _sse(line)
    else:
        pos = 0
    while True:
        await asyncio.sleep(POLL_SECONDS)
        if not path.exists():
            continue
        size = path.stat().st_size
        if size < pos:  # log re-created by tram init --force
            pos = 0
        if size == pos:
            continue
        with path.open("r", encoding="utf-8") as fh:
            fh.seek(pos)
            chunk = fh.read()
            pos = fh.tell()
        for line in chunk.splitlines():
            if line.strip():
                yield _sse(line)


def _sse(raw_line: str) -> str:
    event = TramEvent.model_validate_json(raw_line)
    payload = event.model_dump(mode="json")
    payload["refresh"] = event.kind.value in REFRESH_KINDS
    return f"id: {event.seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def gate_rollup(gate_history: list[GateResult]) -> dict[str, str]:
    rollup: dict[str, str] = defaultdict(str)
    for result in gate_history:
        rollup[result.gate_id] = result.status.value
    return dict(rollup)
