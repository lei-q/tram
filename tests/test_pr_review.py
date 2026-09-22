"""D5 PR review 流：review 状态 -> CR 裁决的确定性映射（gh 为注入的数据源）。"""

import json
import subprocess

import pytest
from typer.testing import CliRunner

from tram.cli import app
from tram.context import load_context
from tram.cr_store import CRStore
from tram.governance import pr_review
from tram.models.cr import CRType
from tram.models.events import EventKind

runner_cli = CliRunner()


def _review(user: str, state: str) -> dict:
    return {"user": {"login": user}, "state": state}


def _proc(stdout: str = "[]", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def _fake_run(responses: dict[str, str]):
    """按 argv 首个元素路由的 fake subprocess.run（git / gh）。"""

    def run(argv, **kwargs):
        key = argv[0]
        if key not in responses:
            raise AssertionError(f"unexpected subprocess: {argv}")
        return _proc(responses[key])

    return run


def _make_cr(ctx, paths: list[str]):
    state = ctx.load_state()
    cr = CRStore(ctx.crs_dir).create_draft(state, CRType.SCOPE, paths, reason="pr flow")
    ctx.state_store.save(ctx.load_state())
    return cr


# ---------- parse_remote ----------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:lei-q/tram.git", "lei-q/tram"),
        ("https://github.com/lei-q/tram.git", "lei-q/tram"),
        ("ssh://git@github.com/lei-q/tram", "lei-q/tram"),
    ],
)
def test_parse_remote(url, expected):
    assert pr_review.parse_remote(url) == expected


def test_parse_remote_rejects_non_github():
    with pytest.raises(ValueError, match="not a github"):
        pr_review.parse_remote("git@gitlab.com:foo/bar.git")


# ---------- summarize_reviews（确定性规则） ----------


def test_summarize_approve_latest_wins():
    reviews = [
        _review("a", "CHANGES_REQUESTED"),
        _review("a", "APPROVED"),
        _review("b", "COMMENTED"),
    ]
    assert pr_review.summarize_reviews(reviews) == "APPROVED"


def test_summarize_changes_requested_blocks():
    reviews = [_review("a", "APPROVED"), _review("b", "CHANGES_REQUESTED")]
    assert pr_review.summarize_reviews(reviews) == "CHANGES_REQUESTED"


def test_summarize_commented_alone_is_not_a_decision():
    assert pr_review.summarize_reviews([_review("a", "COMMENTED")]) is None
    assert pr_review.summarize_reviews([]) is None


# ---------- sync_cr 全链路（fake gh + fake git） ----------


def _wired(ctx, reviews: list[dict]):
    return _fake_run(
        {
            "git": "git@github.com:lei-q/demo.git\n",
            "gh": json.dumps(reviews),
        }
    )


@pytest.fixture
def cr(ctx):
    return _make_cr(ctx, ["src/new_module.py"])


def test_link_and_sync_approve_implements_scope_cr(ctx, cr):
    run = _wired(ctx, [_review("lay", "APPROVED")])
    ref = pr_review.link_pr(ctx, cr.id, 42, run=run)
    assert (ref.repo, ref.number) == ("lei-q/demo", 42)
    assert CRStore(ctx.crs_dir).load(cr.id).pr is not None

    status, detail = pr_review.sync_cr(ctx, cr.id, run=run)
    assert status == "implemented"  # scope CR 批准即并入基线
    assert "lay (gh review)" in detail
    assert "src/new_module.py" in ctx.load_baseline().allowed_paths

    kinds = {(e.kind, e.source) for e in ctx.events.read() if e.refs.get("cr") == cr.id}
    assert (EventKind.HUMAN_DECISION, "tram.github.pr") in kinds


def test_sync_reject_on_changes_requested(ctx, cr):
    run = _wired(ctx, [_review("lay", "CHANGES_REQUESTED")])
    pr_review.link_pr(ctx, cr.id, 7, run=run)
    status, _ = pr_review.sync_cr(ctx, cr.id, run=run)
    assert status == "rejected"
    assert CRStore(ctx.crs_dir).load(cr.id).status.value == "rejected"


def test_sync_without_reviews_is_a_noop(ctx, cr):
    run = _wired(ctx, [_review("lay", "COMMENTED")])
    pr_review.link_pr(ctx, cr.id, 7, run=run)
    status, detail = pr_review.sync_cr(ctx, cr.id, run=run)
    assert status is None
    assert "review" in detail
    assert CRStore(ctx.crs_dir).load(cr.id).status.value == "draft"


def test_sync_needs_link(ctx, cr):
    with pytest.raises(ValueError, match="tram cr link"):
        pr_review.sync_cr(ctx, cr.id, run=_wired(ctx, []))


# ---------- CLI ----------


def test_cli_link_and_sync(ctx, git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    cr = _make_cr(load_context(git_repo), ["src/x.py"])

    monkeypatch.setattr(pr_review, "detect_repo", lambda workspace, run=None: "lei-q/demo")
    monkeypatch.setattr(
        pr_review,
        "fetch_reviews",
        lambda repo, number, run=None: [_review("lay", "APPROVED")],
    )
    assert runner_cli.invoke(app, ["cr", "link", cr.id, "--pr", "42"]).exit_code == 0
    result = runner_cli.invoke(app, ["cr", "sync", cr.id])
    assert result.exit_code == 0
    assert "implemented" in result.output
    assert "lay (gh review)" in result.output


def test_cli_sync_unknown_cr_fails_loud(ctx, git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    result = runner_cli.invoke(app, ["cr", "sync", "cr-9999"])
    assert result.exit_code == 1
    assert "unknown CR" in result.output
