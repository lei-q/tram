"""会话车厢服务 - UI 直接与代码生成引擎多轮对话，全程治理铁轨.

每个会话 = 一条 TaskRecord + 一个常驻 worktree + 引擎会话句柄
（claude --session-id 新建 / --resume 续聊）。每条消息一次 agent run：
跑完即过 Intent Guard——轨内改动由 Tram 提交（commit_refs 留痕），
越界改动拦截并自动立案，与 `tram agent run` 同一款账。引擎只管生成，
判定永远确定性；jobs 供 SSE 流式推送，线程内不复用 UI 的 ctx。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tram.adapters.base import RunnerUnavailableError, RunResult, fold_stream
from tram.adapters.claude_code import ClaudeCodeRunner
from tram.adapters.fake import FakeRunner
from tram.context import TramContext, load_context
from tram.cr_store import CRStore
from tram.governance.intent_guard import IntentGuard, classify_violations
from tram.models.events import EventKind
from tram.models.task import TaskRecord, TaskSpec, TaskStatus
from tram.sandbox.worktree import WorktreeSession

COMMIT_TRAILER = "Co-Authored-By: Claude Code <noreply@anthropic.com>"
ENGINES = ("claude", "fake")
MAX_JOB_LINES = 400


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


@dataclass
class ChatJob:
    """一条消息的一次 agent run：lines 给 SSE 流式，终态给 Guard 账。"""

    id: str
    session_id: str
    task_id: str = ""
    status: str = "running"  # running | ok | blocked | error
    lines: list[dict] = field(default_factory=list)
    summary: str = ""
    error: str | None = None
    commit: str | None = None
    cr: str | None = None


def ui_line(event: dict[str, Any]) -> dict | None:
    """引擎原始事件 -> 车厢里的一行（只留人看得懂的，限长防刷屏）。"""
    kind = event.get("type")
    if kind == "assistant":
        out = []
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text"):
                out.append({"k": "text", "text": str(block["text"])[:400]})
            elif block.get("type") == "tool_use":
                out.append(
                    {
                        "k": "tool",
                        "name": block.get("name"),
                        "input": json.dumps(block.get("input"), ensure_ascii=False)[:160],
                    }
                )
        return out
    if kind == "result":
        return {
            "k": "result",
            "subtype": event.get("subtype"),
            "text": str(event.get("result") or "")[:300],
        }
    if kind == "error":
        return {"k": "error", "text": str(event.get("exit_code", "engine error"))}
    return None


class ChatService:
    """会话注册表 + 消息 job 执行器。UI 与未来的 CLI 共用这一个入口。"""

    def __init__(self, ctx: TramContext, inline: bool = False) -> None:
        self.ctx = ctx
        self.inline = inline  # True：请求内同步执行（测试与调试）
        self.jobs: dict[str, ChatJob] = {}
        self._lock = threading.Lock()

    # ---------- 注册表（.tram/chat/sessions.json） ----------

    def _sessions_file(self) -> Path:
        return self.ctx.repo / ".tram" / "chat" / "sessions.json"

    def _load_sessions(self) -> list[dict]:
        path = self._sessions_file()
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8")).get("sessions", [])

    def _save_sessions(self, sessions: list[dict]) -> None:
        path = self._sessions_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"sessions": sessions}, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def list_sessions(self, include_closed: bool = False) -> list[dict]:
        sessions = self._load_sessions()
        if not include_closed:
            sessions = [s for s in sessions if s["status"] == "open"]
        return sessions

    def get_session(self, session_id: str) -> dict | None:
        return next((s for s in self._load_sessions() if s["id"] == session_id), None)

    def _update_session(self, session_id: str, **fields: Any) -> None:
        sessions = self._load_sessions()
        for s in sessions:
            if s["id"] == session_id:
                s.update(fields)
                break
        self._save_sessions(sessions)

    def open_session(self, engine: str, by: str) -> dict:
        if engine not in ENGINES:
            raise ValueError(f"unknown engine '{engine}' ({' | '.join(ENGINES)})")
        nums = [
            int(m.group(1))
            for s in self._load_sessions()
            if (m := re.fullmatch(r"chat-(\d+)", s["id"]))
        ]
        session = {
            "id": f"chat-{max(nums, default=0) + 1:04d}",
            "engine": engine,
            "engine_session_id": None,
            "task_id": None,
            "branch": None,
            "worktree": None,
            "status": "open",
            "created_by": by,
            "messages": 0,
            "created_at": _now(),
            "updated_at": _now(),
        }
        sessions = self._load_sessions()
        sessions.append(session)
        self._save_sessions(sessions)
        return session

    def close_session(self, session_id: str) -> dict:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        if session["status"] == "closed":
            raise ValueError(f"session {session_id} already closed")
        kept = False
        if session.get("worktree") and Path(session["worktree"]).exists():
            ws = self._worktree(session)
            kept = bool(ws.pending_changes())
            if not kept:
                ws.remove()  # 脏 worktree 永不销毁（未审工作）
        self._update_session(session_id, status="closed", kept_worktree=kept, updated_at=_now())
        # 会话收尾：任务 DONE（返工/QA 另走闭环）
        if session.get("task_id"):
            state = self.ctx.load_state()
            record = state.task(session["task_id"])
            if record and record.status == TaskStatus.DOING:
                record.status = TaskStatus.DONE
                record.spent_points = record.est_points
                self.ctx.state_store.save(state)
        return self.get_session(session_id)  # type: ignore[return-value]

    # ---------- 引擎与 worktree ----------

    def _engine(self, name: str):
        if name == "fake":
            return FakeRunner()
        if name == "claude":
            return ClaudeCodeRunner()
        raise ValueError(f"unknown engine '{name}' ({' | '.join(ENGINES)})")

    def _worktree(self, session: dict) -> WorktreeSession:
        ws = WorktreeSession(self.ctx.repo, session["id"])
        if session.get("worktree"):
            ws.path = Path(session["worktree"])
            ws.branch = session.get("branch") or ws.branch
        return ws

    # ---------- 消息 → job ----------

    def send_message(
        self, session_id: str | None, message: str, by: str, engine: str | None = None
    ) -> tuple[dict, ChatJob]:
        if not message.strip():
            raise ValueError("message is empty")
        if session_id is None:
            session = self.open_session(engine or "claude", by)
        else:
            session = self.get_session(session_id)
            if session is None:
                raise ValueError(f"unknown session: {session_id}")
            if session["status"] != "open":
                raise ValueError(f"session {session_id} is {session['status']}")
        job = ChatJob(id=f"job-{uuid.uuid4().hex[:8]}", session_id=session["id"])
        with self._lock:
            self.jobs[job.id] = job
        work = lambda: self._run_message(session["id"], job, message, by)  # noqa: E731
        if self.inline:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()
        return self.get_session(session["id"]), job  # type: ignore[return-value]

    # ---------- 执行体（线程内） ----------

    def _run_message(self, session_id: str, job: ChatJob, message: str, by: str) -> None:
        ctx = load_context(self.ctx.repo)  # 线程内自己的 ctx，不与 UI 共享可变状态
        try:
            session = self.get_session(session_id)
            assert session is not None
            engine = self._engine(session["engine"])

            state = ctx.load_state()
            task_id = session.get("task_id")
            if task_id is None:  # 首条消息：立任务 + 常驻 worktree
                task_id = _next_task_id(state)
                state.tasks.append(
                    TaskRecord(
                        id=task_id,
                        title=message.splitlines()[0][:80],
                        status=TaskStatus.DOING,
                    )
                )
                ctx.state_store.save(state)  # 先落盘，_finish 重新 load 才看得到
                ws = WorktreeSession(ctx.repo, session_id)
                ws.create()
                self._update_session(
                    session_id,
                    task_id=task_id,
                    branch=ws.branch,
                    worktree=str(ws.path),
                    updated_at=_now(),
                )
            ws = self._worktree(session)
            job.task_id = task_id
            self._update_session(
                session_id, messages=session.get("messages", 0) + 1, updated_at=_now()
            )

            spec = TaskSpec(
                id=task_id,
                prompt=message,
                session_id=session.get("engine_session_id"),
                resume=bool(session.get("engine_session_id")),
            )
            ctx.events.append(
                EventKind.AGENT_RUN_STARTED,
                source="tram.chat",
                data={
                    "task": task_id,
                    "session": session_id,
                    "engine": session["engine"],
                    "by": by,
                    "prompt": message[:500],
                },
                refs={"task": task_id},
            )

            result = self._drive(engine, spec, ws.path, job)

            if result.session_id:  # 引擎会话句柄入库，下条消息 --resume
                self._update_session(
                    session_id, engine_session_id=result.session_id, updated_at=_now()
                )
            self._finish(ctx, session_id, job, ws, result, by)
        except RunnerUnavailableError as exc:
            job.status = "error"
            job.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"

    def _drive(self, engine, spec: TaskSpec, workspace: Path, job: ChatJob) -> RunResult:
        """流式优先：逐事件喂给 job.lines；无 stream 的 runner 走 run() 兜底。"""
        stream = getattr(engine, "stream", None)
        if stream is not None:
            result = fold_stream(self._tap(job, stream(spec, workspace)), engine.name)
        else:
            result = engine.run(spec, workspace)
        result.task_id = spec.id
        return result

    def _tap(self, job: ChatJob, events):
        for ev in events:
            lines = ui_line(ev)
            if lines:
                with self._lock:
                    if len(job.lines) < MAX_JOB_LINES:
                        job.lines.extend(lines)
            yield ev

    def _finish(
        self,
        ctx: TramContext,
        session_id: str,
        job: ChatJob,
        ws: WorktreeSession,
        result: RunResult,
        by: str,
    ) -> None:
        """Guard 铁轨收尾：轨内提交、越界立案（与 `tram agent run` 同款账）。"""
        job.summary = result.summary[:300]
        state = ctx.load_state()
        record = state.task(job.task_id)
        title = record.title if record else session_id
        changed = ws.pending_changes()
        decision = IntentGuard(ctx.load_baseline()).check(changed)

        if not decision.ok:
            job.status = "blocked"
            event = ctx.events.append(
                EventKind.INTENT_BLOCKED,
                source="tram.chat",
                data={
                    "task": job.task_id,
                    "session": session_id,
                    "violations": decision.violations,
                },
                refs={"task": job.task_id},
            )
            cr = CRStore(ctx.crs_dir).create_draft(
                state,
                classify_violations(decision.violations),
                changed_paths=decision.violations,
                reason=f"chat session {session_id} wrote outside scope baseline",
                trigger_event_seq=event.seq,
            )
            ctx.state_store.save(state)
            ctx.events.append(
                EventKind.CR_CREATED,
                source="tram.chat",
                data={"cr": cr.id, "task": job.task_id, "paths": decision.violations},
                refs={"event": str(event.seq), "task": job.task_id},
            )
            ctx.events.append(
                EventKind.AGENT_RUN_FINISHED,
                source="tram.chat",
                data={"task": job.task_id, "session": session_id, "status": "blocked", "cr": cr.id},
                refs={"task": job.task_id, "cr": cr.id},
            )
            job.cr = cr.id
            return

        if changed:
            sha = ws.commit_all(f"tram {session_id}: {title}\n\n{COMMIT_TRAILER}")
            if record is not None and sha:
                record.commit_refs.append(sha)
            ctx.state_store.save(state)
            job.commit = sha
            ctx.events.append(
                EventKind.AGENT_RUN_FINISHED,
                source="tram.chat",
                data={
                    "task": job.task_id,
                    "session": session_id,
                    "status": "ok",
                    "commit": sha or "",
                },
                refs={"task": job.task_id, "commit": sha or ""},
            )
        else:
            ctx.events.append(
                EventKind.AGENT_RUN_FINISHED,
                source="tram.chat",
                data={"task": job.task_id, "session": session_id, "status": "ok", "changes": 0},
                refs={"task": job.task_id},
            )
        job.status = "ok"

    # ---------- job 读侧（API 轮询 / SSE） ----------

    def job_snapshot(self, job_id: str, after: int = 0) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            raise ValueError(f"unknown job: {job_id}")
        with self._lock:
            lines = job.lines[after:]
            return {
                "id": job.id,
                "session": job.session_id,
                "task": job.task_id,
                "status": job.status,
                "lines": lines,
                "next": len(job.lines),
                "summary": job.summary,
                "error": job.error,
                "commit": job.commit,
                "cr": job.cr,
            }


def _next_task_id(state) -> str:
    from tram import operations

    return operations.next_task_id(state)
