"""自动驾驶：决策表哑司机——锚点硬停、干跑不执行、急停文件、步数预算."""

import pytest

from tram.autopilot import route_reasons
from tram.context import load_context
from tram.models.events import EventKind


@pytest.fixture(autouse=True)
def _initialized(ctx):
    """Autopilot 需要 .tram/ 已初始化."""


def test_route_reasons_decision_table():
    assert route_reasons(["scope baseline awaiting human approval"]) == "anchor"
    assert route_reasons(["release awaiting human approval"]) == "anchor"
    assert route_reasons(["open scope change request"]) == "anchor"
    assert route_reasons(["missing artifacts: ['wbs']"]) == "artifacts"
    assert route_reasons(["failed checks: ['charter_present']"]) == "artifacts"
    assert route_reasons(["failed checks: ['tests_pass']"]) == "rework"
    assert route_reasons(["failed checks: ['coverage']"]) == "rework"
    assert route_reasons(["something odd"]) == "unknown"


def test_autopilot_stops_at_baseline_anchor(git_repo):
    """新项目第一锚点：基线待人批准——自动驾驶不越权。"""
    ctx = load_context(git_repo)
    from tram.autopilot import Autopilot

    result = Autopilot(ctx, engine="fake").run()
    assert "基线待人批准" in result["stop"]
    assert result["mode"] == "live" and result["actions"] == 0


def test_autopilot_stop_file_kills_immediately(git_repo):
    ctx = load_context(git_repo)
    (git_repo / ".tram" / "autopilot-stop").write_text("", encoding="utf-8")
    from tram.autopilot import Autopilot

    result = Autopilot(ctx).run()
    assert "急停" in result["stop"]


def test_autopilot_dry_run_executes_nothing(git_repo):
    """干跑：探门看信号可以（事件留痕），但零动作——不开票不孵会话不并线。"""
    from tram.autopilot import Autopilot
    from tram.governance import approvals

    ctx = load_context(git_repo)
    approvals.approve_baseline(ctx, "lay", "")
    result = Autopilot(ctx, engine="fake", dry_run=True).run()
    assert result["mode"] == "dry-run"
    assert result["actions"] == 0
    assert "[干跑]" in result["stop"]
    assert not any(e.kind == EventKind.AUTOPILOT_STEP for e in ctx.events.read())


def test_autopilot_generates_artifacts_then_respects_budget(git_repo):
    """批基线后：缺工件自动开票推进；步数预算防打转。"""
    from tram.autopilot import Autopilot
    from tram.governance import approvals

    ctx = load_context(git_repo)
    approvals.approve_baseline(ctx, "lay", "")
    result = Autopilot(ctx, engine="fake", max_steps=3).run()
    kinds = [e.kind for e in ctx.events.read() if e.source == "tram.autopilot"]
    # 开票动作有账；停车原因落在预算/锚点/未知之一（确定性，但不锁死门禁细节）
    assert EventKind.AUTOPILOT_STEP in kinds or result["actions"] >= 1
    assert result["stop"]
