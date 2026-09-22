"""UI API: state / artifacts / events + SSE live tail; optional HITL approvals.

The UI is the cab window, not a second brain: it reads .tram/ and streams the
black box. 默认只读；`tram ui --approve` 开启审批后，POST /api/approve 仍走
governance.approvals 的同一条写账路径（与 CLI 完全一致），只是把人的决定
写进事件流——不是绕过门禁的按钮。写模式带双 CSRF 防护（会话令牌 + Origin 校验）。
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
from pydantic import BaseModel

from tram.context import TramContext, load_context
from tram.cr_store import CRStore
from tram.governance import approvals
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


def create_app(repo: Path | None = None, allow_approvals: bool = False) -> FastAPI:
    ctx: TramContext = load_context(repo)
    approval_token = secrets.token_urlsafe(24) if allow_approvals else ""
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

    @app.post("/api/approve")
    def api_approve(req: ApprovalRequest, request: Request) -> dict:
        if not allow_approvals:
            raise HTTPException(403, "只读 UI（默认）——`tram ui --approve` 才开启站台审批")
        supplied = request.headers.get("x-tram-token", "")
        if supplied != approval_token or not approval_token:
            raise HTTPException(403, "bad approval token")
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host", ""):
            raise HTTPException(403, f"cross-origin approval rejected: {origin}")
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
