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
import subprocess
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
from tram.governance.intent_guard import IntentGuard, classify_violations, path_matches
from tram.models.events import EventKind
from tram.models.task import TaskRecord, TaskSpec, TaskStatus
from tram.sandbox.worktree import TRAM_IDENTITY, WorktreeSession

COMMIT_TRAILER = "Co-Authored-By: Claude Code <noreply@anthropic.com>"
ENGINES = ("claude", "openhands", "fake")
MAX_JOB_LINES = 400


class MergeRefused(RuntimeError):
    """并线被拒（主工作区脏 / 冲突）——修好环境再来，不是治理结论。"""


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

    # ---------- 聊天历史（.tram/chat/<sid>/log.jsonl，刷新页面不丢） ----------

    def _log_file(self, session_id: str) -> Path:
        return self.ctx.repo / ".tram" / "chat" / session_id / "log.jsonl"

    def append_log(self, session_id: str, entry: dict[str, Any]) -> None:
        path = self._log_file(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**entry, "ts": _now()}, ensure_ascii=False) + "\n")

    def read_log(self, session_id: str) -> list[dict]:
        path = self._log_file(session_id)
        if not path.exists():
            return []
        lines = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                lines.append(obj)
        return lines

    def clear_log(self, session_id: str) -> bool:
        path = self._log_file(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

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

    def open_session(self, engine: str, by: str, wbs_package: str | None = None) -> dict:
        if engine not in ENGINES:
            raise ValueError(f"unknown engine '{engine}' ({' | '.join(ENGINES)})")
        if wbs_package:
            packages = {p["id"] for p in self.ctx.load_wbs()}
            if wbs_package not in packages:
                raise ValueError(f"unknown wbs package: {wbs_package}（见 .tram/wbs.yaml）")
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
            "wbs_package": wbs_package,
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

    def merge_session(self, session_id: str, by: str) -> dict:
        """并线：会话分支经 Guard 预检后合回主线——沙箱产出进项目文件的唯一正门.

        并线本身是治理动作：分支带来的变更集先过 Intent Guard（基线可能在
        会话期间变过），越界照章立案 CR（批准扩基线后重试）；主工作区必须
        干净（不和人手头的工作混）；冲突即中止——确定性工具不裁语义冲突。
        """
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        if not session.get("branch"):
            raise ValueError(f"session {session_id} 还没有产出（先发条消息）")

        # 已跟踪文件的改动 = 人的工作（未跟踪文件不算：.tram/ 账本、init 写的
        # .gitignore 都是治理层自己的落盘，git 也会在碰撞时拒绝覆盖）
        status = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=self.ctx.repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        dirty = [ln[3:] for ln in status.splitlines() if ln and not ln.startswith("??")]
        if dirty:
            raise MergeRefused(
                f"主工作区有 {len(dirty)} 处未提交改动——先 commit/stash 再并线，不混账"
            )

        changed = self._branch_changes(session["branch"])
        if not changed:
            return {
                "merged": False,
                "reason": "nothing",
                "detail": "会话分支没有领先主线的提交，无需并线",
                "paths": [],
            }

        decision = IntentGuard(self.ctx.load_baseline()).check(changed)
        if not decision.ok:
            state = self.ctx.load_state()
            event = self.ctx.events.append(
                EventKind.INTENT_BLOCKED,
                source="tram.chat.merge",
                data={"session": session_id, "violations": decision.violations},
                refs={"task": session.get("task_id") or ""},
            )
            cr = CRStore(self.ctx.crs_dir).create_draft(
                state,
                classify_violations(decision.violations),
                changed_paths=decision.violations,
                reason=f"merge session {session_id}: branch carries out-of-scope paths",
                trigger_event_seq=event.seq,
            )
            self.ctx.state_store.save(state)
            self.ctx.events.append(
                EventKind.CR_CREATED,
                source="tram.chat.merge",
                data={"cr": cr.id, "session": session_id, "paths": decision.violations},
                refs={"event": str(event.seq)},
            )
            return {
                "merged": False,
                "reason": "blocked",
                "detail": "分支带有越界路径，已立案 CR——站台审批放行或扩基线后重试",
                "violations": decision.violations,
                "cr": cr.id,
                "paths": changed,
            }

        # WBS 锚定校验：项目定义了工作包就必须对得上号（自由模式除外）
        anchor_block = self._check_anchor(session, changed)
        if anchor_block is not None:
            return anchor_block

        merge_msg = f"tram: merge session {session_id} ({len(changed)} paths)\n\n{COMMIT_TRAILER}"
        proc = subprocess.run(
            ["git", *TRAM_IDENTITY, "merge", "--no-ff", session["branch"], "-m", merge_msg],
            cwd=self.ctx.repo,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            subprocess.run(["git", "merge", "--abort"], cwd=self.ctx.repo, capture_output=True)
            raise MergeRefused(
                "并线有冲突——确定性工具不裁语义冲突，请手动 `git merge "
                f"{session['branch']}` 解决后重试"
            )

        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.ctx.repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        state = self.ctx.load_state()
        record = state.task(session.get("task_id") or "")
        if record is not None:
            record.commit_refs.append(sha)
            self.ctx.state_store.save(state)
        self.ctx.events.append(
            EventKind.SESSION_MERGED,
            source="tram.chat.merge",
            data={"session": session_id, "commit": sha, "paths": changed, "by": by},
            refs={"commit": sha, "task": session.get("task_id") or ""},
        )
        return {
            "merged": True,
            "commit": sha,
            "paths": changed,
            "anchor": self._anchor_of(session),
            "overlaps": self._session_overlaps(session_id, changed),
        }

    def _anchor_of(self, session: dict) -> str:
        """会话锚定的工作包 id；自由模式返回 'free'。"""
        packages = self.ctx.load_wbs()
        if not packages:
            return "free"
        return session.get("wbs_package") or "free"

    def _check_anchor(self, session: dict, changed: list[str]) -> dict | None:
        """WBS 锚定校验：返回 None 放行，否则返回拒绝结果。"""
        packages = self.ctx.load_wbs()
        if not packages:
            return None  # 自由模式：没有工作包定义，不强制锚定
        pkg_id = session.get("wbs_package")
        if not pkg_id:
            return {
                "merged": False,
                "reason": "unanchored",
                "detail": "项目已定义 WBS 工作包，本会话未锚定——开会话时选工作包（.tram/wbs.yaml）",
                "paths": changed,
            }
        pkg = next((p for p in packages if p["id"] == pkg_id), None)
        if pkg is None:
            return {
                "merged": False,
                "reason": "unanchored",
                "detail": f"工作包 {pkg_id} 已不在 .tram/wbs.yaml 里（规划变更了？）——重新锚定后再并线",  # noqa: E501
                "paths": changed,
            }
        outside = [p for p in changed if not path_matches(p, pkg["paths"])]
        if outside:
            return {
                "merged": False,
                "reason": "anchor_mismatch",
                "detail": f"改动越出工作包 {pkg_id}（{pkg.get('title', '')}）的交付范围",
                "outside": outside,
                "paths": changed,
            }
        return None

    def _session_overlaps(self, session_id: str, changed: list[str]) -> list[dict]:
        """跨会话路径重叠预警：其他在途会话的分支也改了同样的路径（提示，不拦截）。"""
        overlaps: list[dict] = []
        for other in self.list_sessions():
            if other["id"] == session_id or not other.get("branch"):
                continue
            try:
                theirs = self._branch_changes(other["branch"])
            except subprocess.CalledProcessError:
                continue  # 分支可能已被收车清理
            for path in sorted(set(changed) & set(theirs)):
                overlaps.append({"path": path, "session": other["id"]})
        return overlaps

    def _branch_changes(self, branch: str) -> list[str]:
        """分支领先主线的变更路径（merge-base..branch）。"""
        base = subprocess.run(
            ["git", "merge-base", "HEAD", branch],
            cwd=self.ctx.repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        proc = subprocess.run(
            ["git", "diff", "--name-only", base, branch],
            cwd=self.ctx.repo,
            capture_output=True,
            text=True,
            check=True,
        )
        return [p for p in proc.stdout.splitlines() if p.strip()]

    # ---------- 引擎与 worktree ----------

    def _engine(self, name: str):
        if name == "fake":
            return FakeRunner()
        if name == "claude":
            return ClaudeCodeRunner()
        if name == "openhands":
            from tram.adapters.openhands import OpenHandsRunner

            return OpenHandsRunner()
        raise ValueError(f"unknown engine '{name}' ({' | '.join(ENGINES)})")

    def _worktree(self, session: dict) -> WorktreeSession:
        ws = WorktreeSession(self.ctx.repo, session["id"])
        if session.get("worktree"):
            ws.path = Path(session["worktree"])
            ws.branch = session.get("branch") or ws.branch
        return ws

    # ---------- 消息 → job ----------

    def send_message(
        self,
        session_id: str | None,
        message: str,
        by: str,
        engine: str | None = None,
        wbs_package: str | None = None,
    ) -> tuple[dict, ChatJob]:
        if not message.strip():
            raise ValueError("message is empty")
        if session_id is None:
            session = self.open_session(engine or "claude", by, wbs_package)
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
        self.append_log(session_id, {"k": "me", "text": message})
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
                        wbs_package=session.get("wbs_package"),
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
                prompt=self._preamble(ctx, session, task_id) + f"\n\n[用户消息]\n{message}",
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

            if result.status == "error":  # 引擎失败≠治理结论：按 error 落账，不进 Guard 收尾
                job.status = "error"
                job.error = result.summary[:300]
                ctx.events.append(
                    EventKind.AGENT_RUN_FINISHED,
                    source="tram.chat",
                    data={
                        "task": task_id,
                        "session": session_id,
                        "status": "error",
                        "detail": result.summary[:300],
                    },
                    refs={"task": task_id},
                )
                return

            self._finish(ctx, session_id, job, ws, result, by)
        except RunnerUnavailableError as exc:
            job.status = "error"
            job.error = str(exc)
            self.append_log(session_id, {"k": "error", "text": str(exc)})
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            self.append_log(session_id, {"k": "error", "text": job.error or ""})

    def _preamble(self, ctx: TramContext, session: dict, task_id: str) -> str:
        """治理上下文前导——引擎必须知道自己跑在 Tram 的铁轨上.

        纯确定性拼接（阶段/基线/工作流词表），LLM 不进判定路径；
        没有这份上下文，引擎就是辆不知道路权的野车。
        """
        state = ctx.load_state()
        baseline = ctx.load_baseline()
        group_labels = {
            "initiating": "启动",
            "planning": "规划",
            "executing": "执行",
            "monitoring": "监控",
            "closing": "收尾",
        }
        phase_label = group_labels.get(str(state.phase), str(state.phase))
        return "\n".join(
            [
                "[Tram 治理上下文] 你是运行在 Tram（AI coding agent 治理层）管辖下的编码引擎。",
                f"- 项目 {state.project_name} · 会话 {session['id']} · 任务 {task_id} ·"
                f" 当前过程组：{phase_label}（{state.phase}）",
                f"- 工作区是 worktree 沙箱，分支 {session.get('branch')}；主线受保护。",
                "- 工作流：五过程组（启动/规划/执行/监控/收尾）× 十大知识域"
                "（整合/范围/进度/成本/质量/资源/沟通/风险/采购/相关方），G0–G3 门禁放行。",
                "- 阶段流转与门禁裁决由 Tram 掌管（司机在 UI 调度台或 CLI 操作），"
                "你不能自改项目阶段；需要时提醒司机去操作。",
                f"- 基线轨内（允许改）：{', '.join(baseline.allowed_paths) or '（基线为空）'}",
                f"- 基线明令禁止：{', '.join(baseline.forbidden_paths) or '（无）'}",
                "- 越界写入会被 Intent Guard 拦截并自动立案 CR（依赖清单越界属采购域）；"
                "轨内改动由 Tram 自动提交留痕。改文件前先确认路径在轨内。",
                f"- 当前在途任务 {len(state.tasks)} 项、未决 CR {len(state.open_crs)} 项。",
                "回答时结合上述治理状态；用户说「回到某阶段」指的是 Tram 过程组。",
            ]
        )

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
            got = ui_line(ev)
            if got:
                lines = got if isinstance(got, list) else [got]  # ui_line 返回 list 或单 dict
                with self._lock:
                    if len(job.lines) < MAX_JOB_LINES:
                        job.lines.extend(lines)
                for line in lines:  # 聊天历史同步落盘（刷新不丢）
                    self.append_log(job.session_id, line)
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
