"""D5 PR review 流：CR 裁决接 GitHub PR review。

gh api 只是数据源：review 状态 → 裁决的映射是确定性规则（每人取其
最新一条 review；有 changes_requested 即驳回，否则有 approved 即
批准），批准/驳回仍走 approvals.decide_cr 同一条写账路径，
source=tram.github.pr 如实记录"裁决来自 PR review"。
"""

from __future__ import annotations

import json
import re
import subprocess

from tram.context import TramContext
from tram.cr_store import CRStore
from tram.governance import approvals
from tram.models.cr import PRRef

ReviewState = str | None  # APPROVED / CHANGES_REQUESTED / COMMENTED / None


def parse_remote(url: str) -> str:
    """git remote URL -> 'owner/repo'（ssh / https / ssh 协议三种形态）。"""
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?/?$", url)
    if not m or "/" not in m.group(1):
        raise ValueError(f"not a github remote: {url}")
    return m.group(1)


def detect_repo(workspace, run=subprocess.run) -> str:
    proc = run(
        ["git", "remote", "get-url", "origin"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        raise ValueError("no 'origin' remote - PR review 流需要 GitHub 远端")
    return parse_remote(proc.stdout.strip())


def fetch_reviews(repo: str, number: int, run=subprocess.run) -> list[dict]:
    """gh api 拉取 PR reviews（需本机 gh 已登录）。"""
    proc = run(
        ["gh", "api", f"repos/{repo}/pulls/{number}/reviews", "--paginate"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh api failed: {proc.stderr.strip()[:300]}")
    reviews = json.loads(proc.stdout or "[]")
    if not isinstance(reviews, list):
        raise RuntimeError("gh api returned unexpected payload")
    return reviews


def summarize_reviews(reviews: list[dict]) -> ReviewState:
    """确定性规则：每人取最新一条；有 block 即 block，否则有批准即批准。"""
    latest: dict[str, str] = {}
    for review in reviews:  # API 按时间升序，后写覆盖
        reviewer, review_state = review.get("user", {}).get("login", ""), review.get("state", "")
        if reviewer and review_state in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED"):
            latest[reviewer] = review_state
    states = set(latest.values())
    if "CHANGES_REQUESTED" in states:
        return "CHANGES_REQUESTED"
    if "APPROVED" in states:
        return "APPROVED"
    return None


def _first_decisive_reviewer(reviews: list[dict]) -> str:
    for review in reversed(reviews):
        login = review.get("user", {}).get("login", "")
        if login and review.get("state") in ("APPROVED", "CHANGES_REQUESTED"):
            return login
    return "unknown"


def link_pr(ctx: TramContext, cr_id: str, number: int, run=subprocess.run) -> PRRef:
    """CR 关联 PR（repo 从 origin remote 解析，落 CR 文件）。"""
    cr_store = CRStore(ctx.crs_dir)
    cr = cr_store.load(cr_id)
    if cr is None:
        raise ValueError(f"unknown CR: {cr_id}")
    ref = PRRef(repo=detect_repo(ctx.repo, run=run), number=number)
    cr.pr = ref
    cr_store.save(cr)
    return ref


def sync_cr(
    ctx: TramContext, cr_id: str, note: str = "", run=subprocess.run
) -> tuple[str | None, str]:
    """按 PR review 现状裁决 CR。返回 (裁决结果状态, 说明)；无人审则不动。"""
    cr_store = CRStore(ctx.crs_dir)
    cr = cr_store.load(cr_id)
    if cr is None:
        raise ValueError(f"unknown CR: {cr_id}")
    if cr.pr is None:
        raise ValueError(f"{cr_id} 未关联 PR - 先 `tram cr link {cr_id} --pr <number>`")
    reviews = fetch_reviews(cr.pr.repo, cr.pr.number, run=run)
    state = summarize_reviews(reviews)
    if state not in ("APPROVED", "CHANGES_REQUESTED"):
        return None, f"PR #{cr.pr.number} 还没有可用的 review（approve / changes requested）"
    decision = "approved" if state == "APPROVED" else "rejected"
    by = f"{_first_decisive_reviewer(reviews)} (gh review)"
    status = approvals.decide_cr(ctx, cr_id, decision, by=by, note=note, source="tram.github.pr")
    return status.value, f"PR #{cr.pr.number} {state} -> {status.value}（by {by}）"
