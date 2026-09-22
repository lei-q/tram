"""lite 域展开：procurement CR-lite（依赖引入分类）+ 干系人审批人配置。"""

import pytest
from typer.testing import CliRunner

from tram.cli import app
from tram.config import TramConfig
from tram.context import load_context
from tram.cr_store import CRStore
from tram.governance import approvals
from tram.governance.intent_guard import classify_violations
from tram.models.cr import CRStatus, CRType

runner_cli = CliRunner()


# ---------- procurement CR-lite：依赖清单越界 -> procurement CR ----------


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["pyproject.toml"], CRType.PROCUREMENT),
        (["requirements-dev.txt"], CRType.PROCUREMENT),
        (["src/pkg/pyproject.toml"], CRType.PROCUREMENT),  # ** 语义：任意深度
        (["package-lock.json", "src/app.py"], CRType.PROCUREMENT),  # 混合也算采购
        (["src/app.py", "docs/x.md"], CRType.SCOPE),
    ],
)
def test_classify_violations(paths, expected):
    assert classify_violations(paths) is expected


def test_guard_cli_creates_procurement_cr(ctx, git_repo, monkeypatch):
    """依赖清单越界：guard 门红（exit 1）+ 自动 CR 类型为 procurement；批准后并入基线。"""
    monkeypatch.chdir(git_repo)
    result = runner_cli.invoke(
        app,
        ["guard", "check", "requirements.txt", "src/other.py"],
    )
    assert result.exit_code == 1  # 门红：拦截即 exit 1
    assert "procurement" in result.output
    ctx = load_context(git_repo)
    crs = CRStore(ctx.crs_dir).open_crs()
    assert len(crs) == 1
    assert crs[0].type is CRType.PROCUREMENT

    approvals.decide_cr(ctx, crs[0].id, "approved", by="lay")
    assert "requirements.txt" in ctx.load_baseline().allowed_paths
    done = CRStore(ctx.crs_dir).load(crs[0].id)
    assert done.status is CRStatus.IMPLEMENTED


# ---------- 干系人（lite 域）：审批人名单 ----------


def _config_with_approvers(ctx, kind: str, names: list[str]) -> None:
    ctx.config.approvers = {kind: names}
    ctx.config.dump(ctx.tram_dir / "tram.yaml")
    ctx.config = TramConfig.load(ctx.tram_dir / "tram.yaml")


def test_release_approver_enforced(ctx):
    _config_with_approvers(ctx, "release", ["lay"])
    with pytest.raises(ValueError, match="审批人名单"):
        approvals.approve_release(ctx, by="mallory")
    approvals.approve_release(ctx, by="lay")  # 名单内放行
    assert any(a.by == "lay" for a in ctx.load_state().human_approvals)


def test_baseline_approver_enforced(ctx):
    _config_with_approvers(ctx, "baseline", ["lay"])
    with pytest.raises(ValueError, match="审批人名单"):
        approvals.approve_baseline(ctx, by="mallory")


def test_cr_approver_enforced(ctx):
    _config_with_approvers(ctx, "cr", ["lay", "lead"])
    state = ctx.load_state()
    cr = CRStore(ctx.crs_dir).create_draft(state, CRType.SCOPE, ["src/x.py"], reason="t")
    ctx.state_store.save(ctx.load_state())
    with pytest.raises(ValueError, match="审批人名单"):
        approvals.decide_cr(ctx, cr.id, "approved", by="mallory")


def test_unconfigured_kind_stays_open(ctx):
    """未配置的 kind 不限制（向后兼容；CI 机器人场景不受影响）。"""
    _config_with_approvers(ctx, "release", ["lay"])  # 只配了 release
    approvals.approve_baseline(ctx, by="anyone")
    assert ctx.load_state().human_approvals


def test_ui_approval_also_respects_approvers(ctx, git_repo):
    """UI 直连审批走同一服务，名单对 UI 同样生效。"""

    from tests.test_ui_approvals import _writable  # 复用工具函数

    _config_with_approvers(ctx, "release", ["lay"])
    client, token = _writable(git_repo)
    denied = client.post(
        "/api/approve",
        json={"action": "release", "by": "mallory"},
        headers={"X-Tram-Token": token},
    )
    assert denied.status_code == 403  # 名单拒绝 = 403（非 404 兜底）
    assert "审批人名单" in denied.json()["detail"]
