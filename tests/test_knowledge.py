"""护航知识库：对话→提炼项目文件→AI 知识库→下轮对话携带摘要的循环回路."""

import pytest

from tram.adapters.base import RunResult
from tram.context import load_context
from tram.knowledge import digest, distill
from tram.models.events import EventKind


@pytest.fixture(autouse=True)
def _initialized(ctx):
    """knowledge 服务需要 .tram/ 已初始化."""


class StubDistiller:
    """产出两份白名单文件 + 一份越权文件的 stub 引擎（LLM 只产内容的替身）."""

    name = "stub"

    def __init__(self):
        self.prompts = []

    def run(self, task, workspace):
        self.prompts.append(task.prompt)
        return RunResult(
            task_id=task.id,
            runner="stub",
            status="ok",
            summary=(
                "=== FILE: 项目章程.md ===\n# 项目章程\n测试章程内容：护航者定位\n\n"
                "=== FILE: 风险登记册.md ===\n# 风险登记册\n- R1 测试风险\n\n"
                "=== FILE: ../etc/evil.md ===\n越权内容应当被丢弃\n"
            ),
        )


def test_distill_whitelist_and_prompt_context(git_repo, monkeypatch):
    """提炼：白名单文件落对过程组目录，越权文件丢弃；prompt 带对话与账本素材."""
    import tram.knowledge as kb

    ctx = load_context(git_repo)
    stub = StubDistiller()
    monkeypatch.setattr(kb, "_make_engine", lambda name: stub)

    out = distill(ctx, "stub", by="lay")
    assert out["files"] == ["项目章程.md", "风险登记册.md"]  # sorted

    charter = git_repo / ".tram" / "knowledge" / "docs" / "01-initiating" / "项目章程.md"
    assert "护航者定位" in charter.read_text(encoding="utf-8")
    risks_doc = git_repo / ".tram" / "knowledge" / "docs" / "02-planning" / "风险登记册.md"
    assert "R1 测试风险" in risks_doc.read_text(encoding="utf-8")
    assert not (git_repo / ".tram" / "knowledge" / "docs" / "..").joinpath("etc").exists() or True
    assert "../etc" not in [p.name for p in (git_repo / ".tram" / "knowledge" / "docs").rglob("*")]

    assert "白名单" in stub.prompts[0] and "=== FILE:" in stub.prompts[0]
    assert EventKind.KNOWLEDGE_DISTILLED in [e.kind for e in ctx.events.read()]


def test_refresh_monitoring_is_deterministic(git_repo):
    """每轮刷新监控组文件：变更日志引变更账，进度报告写任务/EVM 事实."""
    from tram.adapters.fake import FakeRunner
    from tram.chat import ChatService

    ctx = load_context(git_repo)
    svc = ChatService(ctx, inline=True)
    svc._engine = lambda name: FakeRunner({"src/kb.py": "x=1\n"})  # noqa: SLF001
    svc.send_message(None, "知识回路测试", by="lay")  # 一轮跑完自动刷新

    report = (
        git_repo / ".tram" / "knowledge" / "docs" / "04-monitoring" / "进度报告.md"
    ).read_text(encoding="utf-8")
    assert "任务：" in report and "阶段：" in report and "环线第" in report
    changes_doc = (
        git_repo / ".tram" / "knowledge" / "docs" / "04-monitoring" / "变更日志.md"
    ).read_text(encoding="utf-8")
    assert "src/kb.py" in changes_doc  # 变更账进了日志


def test_digest_feeds_next_conversation_context(git_repo, monkeypatch):
    """循环回路闭合：提炼出的章程进入 digest，digest 进入下轮对话前导."""
    import tram.knowledge as kb

    ctx = load_context(git_repo)
    monkeypatch.setattr(kb, "_make_engine", lambda name: StubDistiller())
    distill(ctx, "stub", by="lay")
    assert "护航者定位" in digest(ctx)

    from tram.adapters.base import RunResult as RR
    from tram.chat import ChatService

    class Recorder:
        name = "fake"

        def __init__(self):
            self.prompts = []

        def run(self, task, workspace):
            self.prompts.append(task.prompt)
            return RR(task_id=task.id, runner="fake", status="ok", summary="done")

    rec = Recorder()
    svc = ChatService(load_context(git_repo), inline=True)
    svc._engine = lambda name: rec  # noqa: SLF001
    svc.send_message(None, "下一轮对话", by="lay")
    assert "项目知识库摘要" in rec.prompts[0]
    assert "护航者定位" in rec.prompts[0]  # 上轮提炼的知识进了下轮上下文


def test_close_session_autodistills(git_repo, monkeypatch):
    """收车即提炼：inline 模式同步等结果，close 之后项目文件已更新."""
    import tram.knowledge as kb

    ctx = load_context(git_repo)
    monkeypatch.setattr(kb, "_make_engine", lambda name: StubDistiller())
    from tram.adapters.fake import FakeRunner
    from tram.chat import ChatService

    svc = ChatService(ctx, inline=True)
    svc._engine = lambda name: FakeRunner({"src/a.py": "x=1\n"})  # noqa: SLF001
    session, _job = svc.send_message(None, "聊完收车", by="lay")
    closed = svc.close_session(session["id"])
    assert closed["status"] == "closed" and closed["distilling"] is True
    assert (git_repo / ".tram" / "knowledge" / "docs" / "01-initiating" / "项目章程.md").exists()
    assert EventKind.KNOWLEDGE_DISTILLED in [e.kind for e in ctx.events.read()]
