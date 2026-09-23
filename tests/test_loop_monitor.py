"""环线与巡检：过程组每圈重复（渐进明细），监控是随车乘务不是一座车站."""

import pytest
from fastapi.testclient import TestClient

from tram import operations
from tram.context import load_context
from tram.models.events import EventKind
from tram.models.state import Phase
from tram.ui.api import create_app


@pytest.fixture(autouse=True)
def _initialized(ctx):
    """需要 .tram/ 已初始化."""


def test_next_lap_folds_back_to_planning(git_repo):
    """环线折返：收尾站发车 → iteration+1、回规划站、事件留痕；别处发车被拒。"""
    ctx = load_context(git_repo)
    state = ctx.load_state()
    state.phase = Phase.CLOSING
    ctx.state_store.save(state)

    result = operations.next_lap(ctx, by="lay")
    assert result == {"iteration": 2, "phase": "planning"}

    fresh = ctx.load_state()
    assert fresh.iteration == 2 and fresh.phase == Phase.PLANNING
    lap_events = [e for e in ctx.events.read() if e.data.get("lap") is True]
    assert lap_events and lap_events[-1].data["iteration"] == 2

    # 再折返一圈：规划站不能直接发车（要先执行、过门到收尾）
    with pytest.raises(ValueError, match="收尾站"):
        operations.next_lap(ctx, by="lay")


def test_monitor_sweep_is_idempotent_per_day(git_repo):
    """巡检乘务：EVM 当天幂等（第二趟跳过）、Guard 核验、风险概览。"""
    ctx = load_context(git_repo)
    first = operations.monitor_sweep(ctx)
    assert first["evm"]["skipped"] is False
    assert first["guard"]["ok"] is True  # 无未提交改动
    assert first["risks"]["open"] == 0

    second = operations.monitor_sweep(ctx)
    assert second["evm"]["skipped"] is True  # 当天已快照，不重复拍
    assert EventKind.EVM_SNAPSHOT in [e.kind for e in ctx.events.read()]


def test_lap_and_monitor_verbs_via_api(git_repo):
    app = create_app(git_repo, allow_approvals=True, inline_jobs=True)
    token = TestClient(app).get("/api/ui-config").json()["token"]
    client = TestClient(app)

    sweep = client.post(
        "/api/action",
        json={"verb": "monitor.sweep", "args": {}, "by": "lay"},
        headers={"X-Tram-Token": token},
    )
    assert sweep.status_code == 200 and sweep.json()["evm"]["skipped"] is False

    # 规划站发车被拒（要在收尾站）；署名必填
    assert (
        client.post(
            "/api/action",
            json={"verb": "lap.next", "args": {}, "by": " "},
            headers={"X-Tram-Token": token},
        ).status_code
        == 422
    )
    resp = client.post(
        "/api/action",
        json={"verb": "lap.next", "args": {}, "by": "lay"},
        headers={"X-Tram-Token": token},
    )
    assert resp.status_code == 400 and "收尾站" in resp.json()["detail"]


def test_guard_block_registers_storm_risk_and_resolves(git_repo):
    """越界拦截入风险登记册（P×I=9 风暴级），人裁流转 watching/closed。"""
    from tram.adapters.fake import FakeRunner
    from tram.chat import ChatService
    from tram.models.risk import RiskStatus

    ctx = load_context(git_repo)
    svc = ChatService(ctx, inline=True)
    svc._engine = lambda name: FakeRunner({"vendor/pyproject.toml": "a=1\n"})  # noqa: SLF001
    _session, job = svc.send_message(None, "越界", by="lay")
    assert job.status == "blocked"

    state = ctx.load_state()
    guard_risks = [r for r in state.risks if r.id == f"r-guard-{job.cr}"]
    assert guard_risks and guard_risks[0].probability * guard_risks[0].impact == 9

    # 幂等：同一 CR 不重复入册（再跑一轮同 CR 不存在——直接验证 id 唯一）
    assert len([r for r in state.risks if r.id.startswith("r-guard-")]) == 1

    out = operations.risk_resolve(ctx, guard_risks[0].id, "watching", by="lay")
    assert out["status"] == "watching"
    assert ctx.load_state().risks[-1].status == RiskStatus.WATCHING

    closed = operations.risk_resolve(ctx, guard_risks[0].id, "closed", by="lay")
    assert closed["status"] == "closed"
    from tram.models.events import EventKind as EK

    assert EK.RISK_RESOLVED in [e.kind for e in ctx.events.read()]

    with pytest.raises(ValueError, match="unknown status"):
        operations.risk_resolve(ctx, guard_risks[0].id, "gone", by="lay")
    with pytest.raises(ValueError, match="unknown risk"):
        operations.risk_resolve(ctx, "r-none", "closed", by="lay")


def test_requirement_gaps_count_unanchored_tasks(git_repo):
    """需求对账：定义了工作包后，未锚定的在途任务要被点名；done 的不算账。"""
    from tram.adapters.fake import FakeRunner
    from tram.chat import ChatService

    ctx = load_context(git_repo)
    assert operations.requirement_gaps(ctx) == {"unanchored": 0, "mode": "free"}

    svc = ChatService(ctx, inline=True)
    svc._engine = lambda name: FakeRunner({"src/a.py": "x=1\n"})  # noqa: SLF001
    svc.send_message(None, "自由任务", by="lay")  # 在途、未锚定

    (git_repo / ".tram" / "wbs.yaml").write_text(
        'packages:\n  - id: WP-001\n    title: t\n    paths: ["src/**"]\n', encoding="utf-8"
    )
    gaps = operations.requirement_gaps(load_context(git_repo))
    assert gaps["mode"] == "anchored" and gaps["unanchored"] == 1

    sweep = operations.monitor_sweep(load_context(git_repo))
    assert sweep["requirements"]["unanchored"] == 1  # 巡检乘务捎上对账
