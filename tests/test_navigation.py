"""领航员：只读护航建议——不执行任何动作，方向盘在驾驶员手里."""

import pytest
from fastapi.testclient import TestClient

from tram.context import load_context
from tram.governance import approvals
from tram.navigation import navigate
from tram.ui.api import create_app


@pytest.fixture(autouse=True)
def _initialized(ctx):
    """navigate 需要 .tram/ 已初始化."""


def test_navigator_advises_without_executing(git_repo):
    """新项目：建议批基线+开票+开工，但一个动作都不替司机执行（零事件、零工件）."""
    ctx = load_context(git_repo)
    before_events = ctx.events.count()

    result = navigate(ctx)
    actions = [r["action"] for r in result["recommendations"]]

    assert "approve-baseline" in actions
    assert "artifact.generate" in actions  # charter 缺
    assert "chat" in actions  # 任务册空
    assert "monitor.sweep" in actions  # 今天没快照
    # 只读铁证：没有事件、没有工件、状态没动
    assert ctx.events.count() == before_events
    assert ctx.artifacts_index.read_text(encoding="utf-8").strip() == "[]"  # 工件索引仍为空


def test_navigator_clear_then_closing_advice(git_repo):
    """仪表清空后：非收尾站建议试探推进；收尾站建议 G3/下一圈二选一."""
    from tram.models.state import Phase

    ctx = load_context(git_repo)
    approvals.approve_baseline(ctx, "lay", "")
    from tram import operations

    operations.artifact_generate(
        ctx, ["charter", "wbs", "schedule", "quality_plan", "risk_register"]
    )
    operations.evm_snapshot(ctx)  # 今天的快照
    state = ctx.load_state()
    from tram.models.task import TaskRecord, TaskStatus

    state.tasks.append(TaskRecord(id="T-001", title="开工", status=TaskStatus.DONE))
    ctx.state_store.save(state)  # 有任务在册，"去开工"建议不再触发

    result = navigate(ctx)
    assert [r["action"] for r in result["recommendations"]] == ["flow.run"]

    state = ctx.load_state()
    state.phase = Phase.CLOSING
    ctx.state_store.save(state)
    result = navigate(ctx)
    assert [r["action"] for r in result["recommendations"]] == ["approve-release", "lap.next"]


def test_navigate_api_readonly(git_repo):
    app = create_app(git_repo, allow_approvals=False)  # 只读模式也能领航
    client = TestClient(app)
    resp = client.get("/api/navigate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["phase"] == "initiating" and body["recommendations"]
