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
from tram.chat import ChatJob, ChatService
from tram.context import load_context
from tram.cr_store import CRStore
from tram.models.events import EventKind
from tram.models.task import TaskSpec
from tram.ui.api import create_app


@pytest.fixture(autouse=True)
def _initialized(ctx):
    """create_app / ChatService 需要 .tram/ 已初始化（与 test_ui_api 同约定）。"""


@pytest.fixture(autouse=True)
def _fast_distill(monkeypatch):
    """收车自动提炼换 FakeRunner——测试绝不摸真引擎（会话 engine 字段常是 claude）。"""
    import tram.knowledge as kb
    from tram.adapters.fake import FakeRunner

    monkeypatch.setattr(kb, "_make_engine", lambda name: FakeRunner())


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


def test_chat_history_persists_until_manually_cleared(git_repo):
    """聊天历史落盘 .tram/chat/<sid>/log.jsonl：刷新不丢，只有手动清空才删。"""
    svc, _ctx = _service(git_repo, {"src/a.py": "x = 1\n"})
    session, _job = svc.send_message(None, "第一条", by="lay")
    svc.send_message(session["id"], "第二条", by="lay")

    lines = svc.read_log(session["id"])
    ks = [ln["k"] for ln in lines]
    assert ks.count("me") == 2
    assert any(k in ks for k in ("text", "result", "tool"))
    assert all("ts" in ln for ln in lines)

    assert svc.clear_log(session["id"]) is True
    assert svc.read_log(session["id"]) == []
    assert svc.clear_log(session["id"]) is False  # 再清是幂等的 no-op


def test_chat_log_api_roundtrip(git_repo):
    app = create_app(git_repo, allow_approvals=True, inline_jobs=True)
    token = TestClient(app).get("/api/ui-config").json()["token"]
    client = TestClient(app)
    sent = client.post(
        "/api/chat/send",
        json={"engine": "fake", "message": "hello", "by": "lay"},
        headers={"X-Tram-Token": token},
    ).json()
    sid = sent["session"]["id"]

    got = client.get(f"/api/chat/sessions/{sid}/log").json()
    assert [ln["k"] for ln in got["lines"]][0] == "me"
    assert client.get("/api/chat/sessions/chat-9999/log").status_code == 404

    assert (
        client.request(
            "DELETE",
            f"/api/chat/sessions/{sid}/log",
            json={"by": " "},
            headers={"X-Tram-Token": token},
        ).status_code
        == 422
    )  # 清空要署名（令牌先过三道闸）
    cleared = client.request(
        "DELETE",
        f"/api/chat/sessions/{sid}/log",
        json={"by": "lay"},
        headers={"X-Tram-Token": token},
    ).json()
    assert cleared["cleared"] is True
    assert client.get(f"/api/chat/sessions/{sid}/log").json()["lines"] == []


def test_merge_session_brings_changes_to_mainline(git_repo):
    """并线正门：轨内产出经 Guard 预检合回主线，事件与 commit_refs 留痕。"""
    svc, ctx = _service(git_repo, {"src/hello.py": "x = 1\n"})
    session, job = svc.send_message(None, "写点东西", by="lay")
    assert job.commit

    main_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True
    ).stdout.strip()
    result = svc.merge_session(session["id"], by="lay")
    assert result["merged"] is True and result["paths"] == ["src/hello.py"]

    main_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True
    ).stdout.strip()
    assert main_after != main_before
    assert (git_repo / "src" / "hello.py").read_text(encoding="utf-8") == "x = 1\n"  # 进主树
    kinds = [e.kind for e in ctx.events.read() if e.source == "tram.chat.merge"]
    assert EventKind.SESSION_MERGED in kinds
    record = ctx.load_state().task(session["task_id"])
    assert result["commit"] in record.commit_refs  # 并线提交也挂在任务账上

    # 没有新产出时再并线是 no-op
    again = svc.merge_session(session["id"], by="lay")
    assert again["merged"] is False and again["reason"] == "nothing"


def test_merge_refused_when_mainline_dirty(git_repo):
    svc, _ctx = _service(git_repo, {"src/hello.py": "x = 1\n"})
    session, _job = svc.send_message(None, "写点东西", by="lay")
    (git_repo / "README.md").write_text("人手头的未提交改动\n", encoding="utf-8")
    from tram.chat import MergeRefused

    with pytest.raises(MergeRefused, match="未提交改动"):
        svc.merge_session(session["id"], by="lay")


def test_merge_blocked_paths_draft_cr(git_repo):
    """基线在会话之后收窄：分支带着越界路径，并线拦截并立案（扩基线后重试即过）。"""
    svc, ctx = _service(git_repo, {"src/hello.py": "x = 1\n"})
    session, _job = svc.send_message(None, "写点东西", by="lay")

    baseline = ctx.load_baseline()
    baseline.allowed_paths = []  # 基线收窄：会话期间被人改过
    baseline.dump(ctx.baseline_file)

    result = svc.merge_session(session["id"], by="lay")
    assert result["merged"] is False and result["reason"] == "blocked"
    cr = CRStore(ctx.crs_dir).load(result["cr"])
    assert cr.type.value == "scope"
    assert not (git_repo / "src" / "hello.py").exists()  # 主线未动

    baseline.allowed_paths = ["src/**"]
    baseline.dump(ctx.baseline_file)
    assert svc.merge_session(session["id"], by="lay")["merged"] is True  # 扩基线后重试即过


def test_merge_api_roundtrip_and_gates(git_repo):
    app = create_app(git_repo, allow_approvals=True, inline_jobs=True)
    token = TestClient(app).get("/api/ui-config").json()["token"]
    client = TestClient(app)
    # 直驱造一个有产出的会话（会话注册表在磁盘上，API 侧同仓可见）
    svc = ChatService(load_context(git_repo), inline=True)
    svc._engine = lambda name: FakeRunner({"src/via_api.py": "y = 2\n"})  # noqa: SLF001
    session, job = svc.send_message(None, "给 API 并线用", by="lay")
    assert job.commit
    sid = session["id"]

    assert (
        client.post(f"/api/chat/sessions/{sid}/merge", json={"by": " "}).status_code == 403
    )  # 无令牌
    assert (
        client.post(
            f"/api/chat/sessions/{sid}/merge", json={"by": " "}, headers={"X-Tram-Token": token}
        ).status_code
        == 422
    )  # 要署名
    merged = client.post(
        f"/api/chat/sessions/{sid}/merge", json={"by": "lay"}, headers={"X-Tram-Token": token}
    ).json()
    assert merged["merged"] is True and "src/via_api.py" in merged["paths"]
    assert client.post("/api/chat/sessions/chat-9999/merge", json={"by": "lay"}).status_code == 403


def test_wbs_anchor_and_overlap_warnings(git_repo):
    """WBS 锚定：产出对得上工作包才放行并线；自由模式降级；跨会话重叠预警。"""
    svc, ctx = _service(git_repo, {"src/auth/login.py": "x = 1\n"})
    # 定义工作包：WP-001 管登录
    (git_repo / ".tram" / "wbs.yaml").write_text(
        'packages:\n  - id: WP-001\n    title: 登录模块\n    paths: ["src/auth/**"]\n',
        encoding="utf-8",
    )

    # 未锚定的会话 → 拒绝并线
    free, _j = svc.send_message(None, "自由发挥", by="lay")
    refused = svc.merge_session(free["id"], by="lay")
    assert refused["merged"] is False and refused["reason"] == "unanchored"

    # 锚定但产出越出工作包 → 拒绝
    svc2 = ChatService(load_context(git_repo), inline=True)
    svc2._engine = lambda name: FakeRunner(
        {"src/auth/login.py": "x=1\n", "src/billing/pay.py": "y=2\n"}
    )  # noqa: SLF001
    wide, _j2 = svc2.send_message(None, "顺手改了计费", by="lay", wbs_package="WP-001")
    mismatch = svc2.merge_session(wide["id"], by="lay")
    assert mismatch["merged"] is False and mismatch["reason"] == "anchor_mismatch"
    assert mismatch["outside"] == ["src/billing/pay.py"]

    # 锚定且对得上 → 放行，且与未并线的 free 会话重叠时给出预警
    svc3 = ChatService(load_context(git_repo), inline=True)
    svc3._engine = lambda name: FakeRunner({"src/auth/login.py": "ok\n"})  # noqa: SLF001
    ok_session, _j3 = svc3.send_message(None, "修登录", by="lay", wbs_package="WP-001")
    merged = svc3.merge_session(ok_session["id"], by="lay")
    assert merged["merged"] is True
    assert merged["anchor"] == "WP-001"
    overlap_paths = {o["path"] for o in merged["overlaps"]}
    assert "src/auth/login.py" in overlap_paths  # free 会话的分支也改了它

    # 删掉工作包定义 → 自由模式回归放行
    (git_repo / ".tram" / "wbs.yaml").unlink()
    svc4 = ChatService(load_context(git_repo), inline=True)
    svc4._engine = lambda name: FakeRunner({"src/anywhere.py": "z=3\n"})  # noqa: SLF001
    free2, _j4 = svc4.send_message(None, "自由模式", by="lay")
    result = svc4.merge_session(free2["id"], by="lay")
    assert result["merged"] is True and result["anchor"] == "free"


def test_open_session_rejects_unknown_package(git_repo):
    svc, _ctx = _service(git_repo, {})
    (git_repo / ".tram" / "wbs.yaml").write_text(
        'packages:\n  - id: WP-001\n    title: t\n    paths: ["src/**"]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unknown wbs package"):
        svc.open_session("fake", "lay", wbs_package="WP-404")


def test_stop_job_aborts_round_without_guard(git_repo):
    """司机急停：引擎进程被终止，本轮作废——不进 Guard、无提交、无知识沉淀。"""
    import time

    class SlowRunner:
        name = "fake"

        def stream(self, task, workspace, stop_event=None):
            for _ in range(200):
                if stop_event is not None and stop_event.is_set():
                    return
                time.sleep(0.01)
                yield {"type": "assistant", "message": {"content": [{"type": "text", "text": "…"}]}}

    svc = ChatService(load_context(git_repo), inline=False)  # 线程模式才能边跑边停
    svc._engine = lambda name: SlowRunner()  # noqa: SLF001 - 测试换装
    session, job = svc.send_message(None, "慢慢来", by="lay")
    for _ in range(200):
        if job.status != "running":
            break
        if job.lines:  # 已经吐出第一行，进入稳态
            svc.stop_job(job.id)
        time.sleep(0.02)
    assert job.status == "stopped"
    assert job.commit is None and job.cr is None
    kinds = [e.kind for e in load_context(git_repo).events.read() if e.source == "tram.chat"]
    assert EventKind.INTENT_BLOCKED not in kinds
    with pytest.raises(ValueError, match="已终态"):
        svc.stop_job(job.id)  # 幂等拒绝


def test_knowledge_collector_writes_three_ledgers(git_repo):
    """知识沉淀：每轮确定性落三本账——需求（消息）/ 变更（提交+知识域）/ 风险（拦截）。"""
    svc, _ctx = _service(git_repo, {"src/hello.py": "x = 1\n"})
    session, job = svc.send_message(None, "做一个登录页", by="lay")
    assert job.status == "ok"

    knowledge = git_repo / ".tram" / "knowledge"
    req = (knowledge / "requirements.md").read_text(encoding="utf-8")
    chg = (knowledge / "changes.md").read_text(encoding="utf-8")
    assert "做一个登录页" in req and session["id"] in req
    assert job.commit[:8] in chg and "src/hello.py" in chg
    assert "知识域" in chg and "整合" in chg  # classify_path 归属进账
    assert not (knowledge / "risks.md").exists()  # 轨内轮次无风险账

    # 越界轮次 → 风险账
    svc2 = ChatService(load_context(git_repo), inline=True)
    svc2._engine = lambda name: FakeRunner({"vendor/pyproject.toml": "a=1\n"})  # noqa: SLF001
    _s2, job2 = svc2.send_message(None, "加依赖", by="lay")
    risks = (knowledge / "risks.md").read_text(encoding="utf-8")
    assert job2.cr in risks and "Intent Guard" in risks


def test_thinking_stream_aggregates_into_lines(git_repo):
    """思考增量流：词级 thinking_delta 聚合成行（攒 200 字或块结束冲出），不逐词刷屏."""
    svc, _ctx = _service(git_repo, {})

    def think_events():
        for i in range(30):
            yield {
                "type": "stream_event",
                "event": {"delta": {"type": "thinking_delta", "thinking": f"思考片段{i}。"}},
            }
        yield {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "想完了"}]},
        }
        yield {"type": "result", "subtype": "success", "session_id": "s", "result": "done"}

    job = ChatJob(id="job-t", session_id="chat-0001")
    list(svc._tap(job, think_events()))  # noqa: SLF001 - 直驱 _tap

    thinks = [ln for ln in job.lines if ln["k"] == "think"]
    total = sum(len(ln["text"]) for ln in thinks)
    assert total == sum(len(f"思考片段{i}。") for i in range(30))  # 一字不丢
    assert len(thinks) < 30  # 聚合过，不是逐词一行
    assert any("思考片段0" in ln["text"] for ln in thinks)
    assert job.lines[-1]["k"] == "result"  # 尾巴也冲掉了，终态行照常


def test_claude_cmd_includes_partial_messages():
    cmd = ClaudeCodeRunner().build_cmd(TaskSpec(id="T-1", prompt="p"))
    assert "--include-partial-messages" in cmd


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
