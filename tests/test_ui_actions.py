"""调度台 /api/action：动词派发到 operations 服务层，与 CLI 同一条写账路径。

读模式三道闸（开关/令牌/Origin）在写端点之间共享；动词回路的断言落在
state + 事件流上——UI 按下的每个按钮都要留下与 CLI 相同的账。
"""

import pytest
import yaml
from fastapi.testclient import TestClient

from tram.context import load_context
from tram.cr_store import CRStore
from tram.models.events import EventKind
from tram.models.task import TaskRecord
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


def _act(
    client: TestClient,
    token: str,
    verb: str,
    args: dict | None = None,
    by: str = "",
    origin: str | None = None,
):
    headers = {"X-Tram-Token": token}
    if origin:
        headers["Origin"] = origin
    return client.post(
        "/api/action", json={"verb": verb, "args": args or {}, "by": by}, headers=headers
    )


# ---------- 写模式三道闸 ----------


def test_readonly_action_403(git_repo):
    client = _client(git_repo, allow=False)
    resp = _act(client, "", "evm.snapshot")
    assert resp.status_code == 403
    assert "只读" in resp.json()["detail"]


def test_action_requires_token(git_repo):
    client, token = _writable(git_repo)
    assert _act(client, "", "evm.snapshot").status_code == 403
    assert _act(client, "wrong", "evm.snapshot").status_code == 403


def test_action_rejects_cross_origin(git_repo):
    client, token = _writable(git_repo)
    resp = _act(client, token, "evm.snapshot", origin="https://evil.example")
    assert resp.status_code == 403
    assert "cross-origin" in resp.json()["detail"]


def test_unknown_verb_400(git_repo):
    client, token = _writable(git_repo)
    resp = _act(client, token, "self.destruct")
    assert resp.status_code == 400
    assert "unknown verb" in resp.json()["detail"]


def test_signed_verbs_require_by(git_repo):
    client, token = _writable(git_repo)
    for verb in ("qa.fail", "qa.pass", "baseline.save"):
        resp = _act(client, token, verb, {"task": "T-001", "yaml": "version: 1"})
        assert resp.status_code == 422
        assert "署名" in resp.json()["detail"]


# ---------- 动词回路：state + 事件流都要留账 ----------


def test_gate_run_roundtrip(git_repo):
    client, token = _writable(git_repo)
    resp = _act(client, token, "gate.run", {"gate": "g0_charter_gate"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["gate"] == "g0_charter_gate"
    assert body["status"] in {"pass", "fail", "blocked_pending_human"}

    ctx = load_context(git_repo)
    assert any(e.kind == EventKind.GATE_EVALUATED for e in ctx.events.read())
    assert ctx.load_state().gate_history[-1].gate_id == "g0_charter_gate"


def test_guard_check_violation_drafts_procurement_cr(git_repo):
    client, token = _writable(git_repo)
    resp = _act(client, token, "guard.check", {"paths": ["vendor/pyproject.toml"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["cr_type"] == "procurement"
    assert body["cr"] and body["cr"].startswith("cr-")
    assert "vendor/pyproject.toml" in body["violations"]

    ctx = load_context(git_repo)
    crs = CRStore(ctx.crs_dir).open_crs()
    assert [cr.id for cr in crs] == [body["cr"]]
    kinds = [e.kind for e in ctx.events.read()]
    assert EventKind.INTENT_BLOCKED in kinds and EventKind.CR_CREATED in kinds


def test_guard_check_within_baseline(git_repo):
    client, token = _writable(git_repo)
    resp = _act(client, token, "guard.check", {"paths": ["src/tram/main.py"]})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_task_points_and_qa_loop(git_repo):
    ctx = load_context(git_repo)
    state = ctx.load_state()
    state.tasks.append(TaskRecord(id="T-001", title="搭站台", est_points=2.0))
    ctx.state_store.save(state)

    client, token = _writable(git_repo)
    resp = _act(client, token, "task.points", {"task": "T-001", "est": 3, "spent": 1})
    assert resp.status_code == 200
    assert resp.json()["est"] == 3 and resp.json()["spent"] == 1

    resp = _act(client, token, "qa.fail", {"task": "T-001", "note": "信号灯不亮"}, by="lay")
    assert resp.status_code == 200
    rework_id = resp.json()["rework_task"]
    rework = ctx.load_state().task(rework_id)
    assert rework.rework_of == "T-001"
    assert rework.est_points == 3  # 返工任务继承上游点数

    resp = _act(client, token, "qa.pass", {"task": "T-001"}, by="lay")
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"

    kinds = [e.kind for e in ctx.events.read()]
    assert {EventKind.TASK_UPDATED, EventKind.QA_FAILED, EventKind.QA_PASSED} <= set(kinds)


def test_artifact_generate_all_and_unknown_kind(git_repo):
    client, token = _writable(git_repo)
    resp = _act(client, token, "artifact.generate", {"all": True})
    assert resp.status_code == 200
    kinds = {a["kind"] for a in resp.json()["artifacts"]}
    assert {"charter", "scope_baseline"} <= kinds

    resp = _act(client, token, "artifact.generate", {"kinds": ["nope"]})
    assert resp.status_code == 400
    assert "unknown kinds" in resp.json()["detail"]


def test_evm_snapshot_action(git_repo):
    client, token = _writable(git_repo)
    resp = _act(client, token, "evm.snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"date", "spi", "cpi", "reasons", "new_risks"}
    assert any(e.kind == EventKind.EVM_SNAPSHOT for e in load_context(git_repo).events.read())


def test_baseline_save_roundtrip(git_repo):
    client, token = _writable(git_repo)
    current = client.get("/api/baseline").json()
    assert current["yaml"]
    data = yaml.safe_load(current["yaml"])
    data["allowed_paths"] = sorted(set(data["allowed_paths"]) | {"docs/**"})

    resp = _act(
        client,
        token,
        "baseline.save",
        {"yaml": yaml.safe_dump(data, allow_unicode=True)},
        by="lay",
    )
    assert resp.status_code == 200
    assert "docs/**" in client.get("/api/baseline").json()["allowed"]

    events = [e for e in load_context(git_repo).events.read() if e.kind == EventKind.BASELINE_SAVED]
    assert len(events) == 1
    assert events[0].source == "tram.baseline"
    assert events[0].data["by"] == "lay"


def test_baseline_save_rejects_schema_violation(git_repo):
    client, token = _writable(git_repo)
    bad = yaml.safe_dump({"version": 1, "allowed_paths": "not-a-list"})
    resp = _act(client, token, "baseline.save", {"yaml": bad}, by="lay")
    assert resp.status_code == 400
    # 校验失败不落盘：基线保持原样
    assert "docs/**" not in client.get("/api/baseline").json()["allowed"]


def test_snapshot_includes_task_list(git_repo):
    ctx = load_context(git_repo)
    state = ctx.load_state()
    state.tasks.append(TaskRecord(id="T-001", title="铺轨", est_points=2.0))
    ctx.state_store.save(state)

    client, token = _writable(git_repo)
    snap = client.get("/api/state").json()
    assert snap["task_list"][0]["id"] == "T-001"
    assert snap["task_list"][0]["est"] == 2
