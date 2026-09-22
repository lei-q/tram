"""会话车厢：UI 直接与引擎多轮对话，全程治理铁轨。

单元层直驱 ChatService（换装 FakeRunner 的写盘计划）：任务/常驻
worktree/引擎会话句柄/Guard 收尾/收车语义；API 层验证写模式三道闸
与 SSE 终态。越界立案与 `tram agent run` 同一款账。
"""

import json
import stat
import subprocess

import pytest
from fastapi.testclient import TestClient

from tram.adapters.base import RunnerUnavailableError, RunResult
from tram.adapters.claude_code import ClaudeCodeRunner
from tram.adapters.fake import FakeRunner
from tram.chat import ChatService
from tram.context import load_context
from tram.cr_store import CRStore
from tram.models.events import EventKind
from tram.models.task import TaskSpec
from tram.ui.api import create_app


@pytest.fixture(autouse=True)
def _initialized(ctx):
    """create_app / ChatService 需要 .tram/ 已初始化（与 test_ui_api 同约定）。"""


# ---------- 适配器：会话旗标与二进制定位 ----------


def test_resolve_binary_falls_back_to_common_dirs(tmp_path, monkeypatch):
    """IDE/桌面进程 PATH 缺 ~/.local/bin 时，仍要能从常见安装位找到引擎。"""
    from tram.adapters import base

    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(base.shutil, "which", lambda name: None)
    monkeypatch.setattr(base, "COMMON_BIN_DIRS", (tmp_path,))

    assert base.resolve_binary("claude") == str(fake)
    assert base.resolve_binary("nope") is None


def test_claude_unavailable_lists_searched_paths(tmp_path):
    with pytest.raises(RunnerUnavailableError, match="not found"):
        list(
            ClaudeCodeRunner(binary="/nonexistent/claude").stream(
                TaskSpec(id="T-1", prompt="p"), tmp_path
            )
        )


def test_claude_build_cmd_sandbox_grants_tools():
    """沙箱即边界：默认放开工具权限（headless 没人应答写许可，claude 会全拒）。"""
    cmd = ClaudeCodeRunner().build_cmd(TaskSpec(id="T-1", prompt="p"))
    assert "--dangerously-skip-permissions" in cmd

    cmd = ClaudeCodeRunner().build_cmd(TaskSpec(id="T-1", prompt="p", allowed_tools=["Read"]))
    assert "--dangerously-skip-permissions" not in cmd  # 显式白名单时尊重限制
    assert "--allowedTools" in cmd and cmd[cmd.index("--allowedTools") + 1] == "Read"


def test_claude_build_cmd_session_flags():
    cmd = ClaudeCodeRunner().build_cmd(TaskSpec(id="T-1", prompt="p", session_id="s-1"))
    assert "--session-id" in cmd and cmd[cmd.index("--session-id") + 1] == "s-1"
    cmd = ClaudeCodeRunner().build_cmd(
        TaskSpec(id="T-1", prompt="p", session_id="s-1", resume=True)
    )
    assert "--resume" in cmd and cmd[cmd.index("--resume") + 1] == "s-1"
    assert "--session-id" not in cmd  # 续聊不带 session-id


def test_claude_consume_folds_trace_and_session():
    events = [
        {"type": "system", "subtype": "init"},
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"x": 1}}]},
        },
        {
            "type": "result",
            "subtype": "success",
            "session_id": "sess-9",
            "result": "done",
        },
    ]
    result = ClaudeCodeRunner.consume(events)
    assert result.status == "ok"
    assert result.tool_trace[0]["name"] == "Edit"
    assert result.session_id == "sess-9"


# ---------- ChatService 单元：任务 / worktree / Guard 收尾 ----------


def _service(git_repo, writes):
    ctx = load_context(git_repo)
    svc = ChatService(ctx, inline=True)
    svc._engine = lambda name: FakeRunner(writes)  # noqa: SLF001 - 测试换装
    return svc, ctx


def test_message_creates_task_worktree_and_commit(git_repo):
    svc, ctx = _service(git_repo, {"src/hello.py": "x = 1\n"})
    session, job = svc.send_message(None, "hello tram", by="lay")
    assert job.status == "ok" and job.commit

    session = svc.get_session(session["id"])
    assert session["task_id"] and session["engine_session_id"]  # 引擎句柄已入库
    assert session["messages"] == 1

    # 提交落在 tram/<session> 分支，主线 HEAD 不动
    head = subprocess.run(
        ["git", "log", "--format=%H", "-1", session["branch"]],
        cwd=git_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == job.commit
    main_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True
    ).stdout.strip()
    assert main_head != job.commit

    # 第二条消息：同任务同 worktree 续聊
    _s2, job2 = svc.send_message(session["id"], "再补个测试", by="lay")
    assert job2.status == "ok"
    assert job2.task_id == session["task_id"]
    assert svc.get_session(session["id"])["messages"] == 2

    kinds = [e.kind for e in ctx.events.read() if e.source == "tram.chat"]
    assert EventKind.AGENT_RUN_STARTED in kinds
    assert EventKind.AGENT_RUN_FINISHED in kinds


def test_out_of_baseline_message_blocked_and_cr_drafted(git_repo):
    svc, ctx = _service(git_repo, {"vendor/pyproject.toml": "a=1\n"})
    session, job = svc.send_message(None, "偷偷加依赖", by="lay")
    assert job.status == "blocked" and job.cr
    assert not (git_repo / "vendor").exists()  # 越界内容在 worktree 里，不进主线

    cr = CRStore(ctx.crs_dir).load(job.cr)
    assert cr.type.value == "procurement"

    # 脏 worktree 保留（未审工作不销毁）
    from pathlib import Path

    assert Path(session["worktree"]).exists()
    kinds = [e.kind for e in ctx.events.read() if e.source == "tram.chat"]
    assert EventKind.INTENT_BLOCKED in kinds and EventKind.CR_CREATED in kinds


def test_close_clean_session_removes_worktree_and_closes_task(git_repo):
    svc, ctx = _service(git_repo, {"src/hello.py": "x = 1\n"})
    session, _job = svc.send_message(None, "hello", by="lay")
    from pathlib import Path

    assert Path(session["worktree"]).exists()

    closed = svc.close_session(session["id"])
    assert closed["status"] == "closed"
    assert not Path(session["worktree"]).exists()  # 干净 → 移除

    record = ctx.load_state().task(session["task_id"])
    assert record.status.value == "done"  # 会话收尾任务 DONE


def test_chat_on_repo_without_commits_bootstraps_worktree(tmp_path):
    """零提交仓库（unborn HEAD）挂不了 worktree——首条消息自动补空引导提交再挂。"""
    repo = tmp_path / "bare"  # autouse fixture 已占了 tmp_path/repo
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    from tram.context import init_project

    ctx = init_project(repo, name="demo")
    svc = ChatService(ctx, inline=True)
    svc._engine = lambda name: FakeRunner({"src/hello.py": "x = 1\n"})  # noqa: SLF001 - 测试换装
    session, job = svc.send_message(None, "hello", by="lay")
    assert job.status == "ok" and job.commit

    # 引导提交落在 main 打底，会话分支照常从它挂出
    head = subprocess.run(
        ["git", "log", "--format=%s", "-1", "main"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert "bootstrap" in head


def test_message_prompt_carries_governance_preamble(git_repo):
    """引擎收到的 prompt 带治理上下文——不知道自己在 Tram 铁轨上就谈不上被治理."""

    class Recorder:
        name = "fake"

        def __init__(self):
            self.prompts = []

        def run(self, task, workspace):
            self.prompts.append(task.prompt)
            return RunResult(task_id=task.id, runner="fake", status="ok", summary="done")

    rec = Recorder()
    svc, _ctx = _service(git_repo, {})
    svc._engine = lambda name: rec  # noqa: SLF001 - 测试换装
    svc.send_message(None, "回到启动阶段", by="lay")

    prompt = rec.prompts[0]
    assert prompt.startswith("[Tram 治理上下文]")
    assert "五过程组" in prompt and "十大知识域" in prompt
    assert "G0–G3" in prompt
    assert "[用户消息]\n回到启动阶段" in prompt  # 用户消息原样殿后
    # 基线路径进上下文（引擎据此判断轨内轨外）
    baseline_allowed = load_context(git_repo).load_baseline().allowed_paths
    for p in baseline_allowed:
        assert p in prompt


def test_close_dirty_session_keeps_worktree(git_repo):
    svc, _ctx = _service(git_repo, {"vendor/pyproject.toml": "a=1\n"})
    session, _job = svc.send_message(None, "越界", by="lay")
    closed = svc.close_session(session["id"])
    assert closed["status"] == "closed"
    assert closed["kept_worktree"] is True


def test_engine_error_marks_job_error_not_guard(git_repo):
    """引擎 run 失败 ≠ 治理结论：job error、AGENT_RUN_FINISHED(error)，不进 Guard 收尾。"""

    class ErrorRunner:
        name = "fake"

        def run(self, task, workspace):  # 无 stream → _drive 走 run() 兜底
            return RunResult(task_id=task.id, runner="fake", status="error", summary="boom")

    svc, ctx = _service(git_repo, {})
    svc._engine = lambda name: ErrorRunner()  # noqa: SLF001 - 测试换装
    session, job = svc.send_message(None, "hi", by="lay")
    assert job.status == "error" and "boom" in (job.error or "")

    finished = [
        e
        for e in ctx.events.read()
        if e.source == "tram.chat" and e.kind == EventKind.AGENT_RUN_FINISHED
    ]
    assert finished and finished[-1].data["status"] == "error"
    kinds = [e.kind for e in ctx.events.read() if e.source == "tram.chat"]
    assert EventKind.INTENT_BLOCKED not in kinds and EventKind.CR_CREATED not in kinds


def test_unknown_session_and_closed_session_rejected(git_repo):
    svc, _ctx = _service(git_repo, {})
    with pytest.raises(ValueError, match="unknown session"):
        svc.send_message("chat-9999", "hi", by="lay")

    session, _job = svc.send_message(None, "hi", by="lay")
    svc.close_session(session["id"])
    with pytest.raises(ValueError, match="closed"):
        svc.send_message(session["id"], "hi again", by="lay")


# ---------- API：三道闸 + SSE ----------


def test_chat_send_requires_write_mode(git_repo):
    client = TestClient(create_app(git_repo, allow_approvals=False, inline_jobs=True))
    resp = client.post("/api/chat/send", json={"message": "hi", "by": "lay"})
    assert resp.status_code == 403
    assert "只读" in resp.json()["detail"]


def test_chat_send_requires_token_and_signature(git_repo):
    app = create_app(git_repo, allow_approvals=True, inline_jobs=True)
    token = TestClient(app).get("/api/ui-config").json()["token"]
    client = TestClient(app)
    assert (
        client.post(
            "/api/chat/send",
            json={"message": "hi", "by": "lay"},
            headers={"X-Tram-Token": "wrong"},
        ).status_code
        == 403
    )
    resp = client.post(
        "/api/chat/send", json={"message": "hi", "by": " "}, headers={"X-Tram-Token": token}
    )
    assert resp.status_code == 422
    assert "署名" in resp.json()["detail"]


def test_chat_send_roundtrip_and_unknown_session(git_repo):
    app = create_app(git_repo, allow_approvals=True, inline_jobs=True)
    token = TestClient(app).get("/api/ui-config").json()["token"]
    client = TestClient(app)
    resp = client.post(
        "/api/chat/send",
        json={"engine": "fake", "message": "hello", "by": "lay"},
        headers={"X-Tram-Token": token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session"]["id"].startswith("chat-")
    assert body["job"]["status"] == "ok"

    resp = client.post(
        "/api/chat/send",
        json={"session": "chat-4242", "message": "hi", "by": "lay"},
        headers={"X-Tram-Token": token},
    )
    assert resp.status_code == 404


def test_chat_open_rejects_unknown_engine(git_repo):
    app = create_app(git_repo, allow_approvals=True, inline_jobs=True)
    token = TestClient(app).get("/api/ui-config").json()["token"]
    resp = TestClient(app).post(
        "/api/chat/sessions",
        json={"engine": "gpt-9000", "by": "lay"},
        headers={"X-Tram-Token": token},
    )
    assert resp.status_code == 400


def test_chat_job_404(git_repo):
    client = TestClient(create_app(git_repo, allow_approvals=True, inline_jobs=True))
    assert client.get("/api/chat/jobs/job-nope").status_code == 404


def test_chat_stream_ends_with_done(git_repo):
    app = create_app(git_repo, allow_approvals=True, inline_jobs=True)
    token = TestClient(app).get("/api/ui-config").json()["token"]
    client = TestClient(app)
    sent = client.post(
        "/api/chat/send",
        json={"engine": "fake", "message": "hello", "by": "lay"},
        headers={"X-Tram-Token": token},
    ).json()
    events = []
    with client.stream("GET", "/api/chat/stream", params={"job": sent["job"]["id"]}) as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    assert events[-1]["k"] == "done"
    assert events[-1]["job"]["status"] == "ok"
