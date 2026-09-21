"""Read-only UI API: state / artifacts / events + SSE live tail.

The UI is the cab window, not a second brain: it only reads .tram/ and
streams the black box. Every mutation stays in the CLI (and its gates).
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from tram.context import TramContext, load_context
from tram.cr_store import CRStore
from tram.governance.gate_runner import load_gate_specs
from tram.models.artifacts import Artifact
from tram.models.events import TramEvent
from tram.models.gates import GateResult
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
}


def create_app(repo: Path | None = None) -> FastAPI:
    ctx: TramContext = load_context(repo)
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
        return {
            "project_name": state.project_name,
            "phase": state.phase.value,
            "sandbox": ctx.config.sandbox.value,
            "policy_engine": ctx.config.policy_engine.value,
            "events_count": ctx.events.count(),
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
