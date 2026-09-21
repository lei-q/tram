"""UI 审批直连事件流：默认只读；--approve 后走与 CLI 相同的写账路径。"""

import pytest
from fastapi.testclient import TestClient

from tram.models.events import EventKind
from tram.ui.api import create_app


@pytest.fixture(autouse=True)
def _initialized(ctx):
    """create_app 需要 .tram/ 已初始化（与 test_ui_api 同约定）。"""


def _client(git_repo, allow: bool) -> TestClient:
    return TestClient(create_app(git_repo, allow_approvals=allow))


def _writable(git_repo) -> tuple[TestClient, str]:
    app = create_app(git_repo, allow_approvals=True)
    token = TestClient(app).get("/api/ui-config").json()["token"]
    return TestClient(app), token


def _post(client: TestClient, token: str, payload: dict, origin: str | None = None):
    headers = {"X-Tram-Token": token}
    if origin:
        headers["Origin"] = origin
    return client.post("/api/approve", json=payload, headers=headers)


def test_default_ui_is_read_only(git_repo):
    client = _client(git_repo, allow=False)
    config = client.get("/api/ui-config").json()
    assert config["approvals_enabled"] is False
    resp = client.post("/api/approve", json={"action": "release", "by": "lay"})
    assert resp.status_code == 403
    assert "只读" in resp.json()["detail"]


def test_approval_requires_token(git_repo):
    client, token = _writable(git_repo)
    assert _post(client, "", {"action": "release", "by": "lay"}).status_code == 403
    assert _post(client, "wrong", {"action": "release", "by": "lay"}).status_code == 403


def test_approval_rejects_cross_origin(git_repo):
    client, token = _writable(git_repo)
    resp = _post(
        client,
        token,
        {"action": "release", "by": "lay"},
        origin="https://evil.example",
    )
    assert resp.status_code == 403
    assert "cross-origin" in resp.json()["detail"]


def test_approval_requires_signature(git_repo):
    client, token = _writable(git_repo)
    resp = _post(client, token, {"action": "release", "by": "  "})
    assert resp.status_code == 422
    assert "署名" in resp.json()["detail"]


def test_release_approval_writes_event_stream(git_repo):
    """UI 放行 = 与 CLI 同一条写账路径：state 审批记录 + HUMAN_DECISION。"""
    client, token = _writable(git_repo)
    resp = _post(client, token, {"action": "release", "by": "lay", "note": "ok to ship"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    from tram.context import load_context

    ctx = load_context(git_repo)
    state = ctx.load_state()
    assert any(
        a.kind == "release" and a.by == "lay" and a.decision == "approved"
        for a in state.human_approvals
    )
    decisions = [e for e in ctx.events.read() if e.kind == EventKind.HUMAN_DECISION]
    assert len(decisions) == 1
    assert decisions[0].source == "tram.ui"
    assert decisions[0].data["by"] == "lay"


def test_baseline_approval_updates_state(git_repo):
    client, token = _writable(git_repo)
    resp = _post(client, token, {"action": "baseline", "by": "lay"})
    assert resp.status_code == 200
    assert client.get("/api/state").json()["scope_approved"] is True


def test_cr_decision_unknown_id_404(git_repo):
    client, token = _writable(git_repo)
    resp = _post(client, token, {"action": "cr_approve", "id": "cr-9999", "by": "lay"})
    assert resp.status_code == 404
    assert "unknown CR" in resp.json()["detail"]


def test_cr_decision_requires_id(git_repo):
    client, token = _writable(git_repo)
    resp = _post(client, token, {"action": "cr_approve", "by": "lay"})
    assert resp.status_code == 422


@pytest.mark.parametrize("action", ["cr_approve", "cr_reject"])
def test_cr_decision_roundtrip(git_repo, action):
    """拦截产生的 CR 就地裁决：状态推进 + 事件流两条记录（CR 状态 + 人工决定）。"""
    from tram.context import load_context
    from tram.cr_store import CRStore
    from tram.models.cr import CRType
    from tram.models.events import EventKind

    ctx = load_context(git_repo)
    cr = CRStore(ctx.crs_dir).create_draft(
        ctx.load_state(),
        CRType.SCOPE,
        ["src/new_thing.py"],
        reason="ui roundtrip",
    )
    ctx.state_store.save(ctx.load_state())

    client, token = _writable(git_repo)
    resp = _post(
        client,
        token,
        {"action": action, "id": cr.id, "by": "lay", "note": "from platform"},
    )
    assert resp.status_code == 200

    reloaded = CRStore(ctx.crs_dir).load(cr.id)
    if action == "cr_reject":
        assert reloaded.status.value == "rejected"
    else:  # scope CR 批准即并入基线并视为已实施
        assert reloaded.status.value == "implemented"
        assert "src/new_thing.py" in ctx.load_baseline().allowed_paths

    kinds = [e.kind for e in ctx.events.read() if e.refs.get("cr") == cr.id]
    assert EventKind.CR_STATUS_CHANGED in kinds
    assert EventKind.HUMAN_DECISION in kinds
