"""文件车厢：列/读开放（只读默认即安全），保存走与 agent 相同的 Guard 铁轨。

断言落点：越界保存被拦 + 自动立案（与 `tram guard check` 同款账），
轨内保存落盘 + FILE_SAVED 事件带署名；.tram/.git 结构保护谁都不能写。
"""

import pytest
from fastapi.testclient import TestClient

from tram.context import load_context
from tram.cr_store import CRStore
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


def _save(client: TestClient, token: str, path: str, content: str, by: str = "lay"):
    return client.post(
        "/api/file",
        json={"path": path, "content": content, "by": by},
        headers={"X-Tram-Token": token},
    )


# ---------- 列目录 / 读文件（只读默认即可用） ----------


def test_list_root_shows_project_files_hides_git(git_repo):
    client = _client(git_repo, allow=False)
    entries = client.get("/api/files").json()["entries"]
    names = {e["name"]: e for e in entries}
    assert names["README.md"]["type"] == "file"
    assert names[".tram"]["type"] == "dir"
    assert ".git" not in names  # git 内部目录不进车厢


def test_list_subdir_and_changed_marker(git_repo):
    (git_repo / "src").mkdir()
    (git_repo / "src" / "track.py").write_text("x = 1\n", encoding="utf-8")
    client = _client(git_repo, allow=False)
    entries = client.get("/api/files", params={"path": "src"}).json()["entries"]
    assert [e["name"] for e in entries] == ["track.py"]
    assert entries[0]["changed"] is True  # untracked → pending changes → ●


def test_list_rejects_escape_and_abs_path(git_repo):
    client = _client(git_repo, allow=False)
    assert client.get("/api/files", params={"path": "../"}).status_code == 400
    assert client.get("/api/files", params={"path": "/etc"}).status_code == 400
    assert client.get("/api/files", params={"path": "nope/nope"}).status_code == 400


def test_read_file_with_baseline_status(git_repo):
    client = _client(git_repo, allow=False)
    body = client.get("/api/file", params={"path": "README.md"}).json()
    assert body["content"] == "demo\n"
    assert body["in_baseline"] is True
    assert body["binary"] is False


def test_read_out_of_baseline_flags_violation(git_repo):
    (git_repo / "vendor").mkdir()
    (git_repo / "vendor" / "x.toml").write_text("a=1\n", encoding="utf-8")
    client = _client(git_repo, allow=False)
    body = client.get("/api/file", params={"path": "vendor/x.toml"}).json()
    assert body["in_baseline"] is False
    assert body["violations"] == ["vendor/x.toml"]


def test_read_binary_reports_no_content(git_repo):
    (git_repo / "logo.png").write_bytes(b"PNG\r\n\x00\x01\x02")
    client = _client(git_repo, allow=False)
    body = client.get("/api/file", params={"path": "logo.png"}).json()
    assert body["binary"] is True
    assert body["content"] is None


def test_read_missing_or_dir_404(git_repo):
    client = _client(git_repo, allow=False)
    assert client.get("/api/file", params={"path": "ghost.md"}).status_code == 404
    assert client.get("/api/file", params={"path": ".tram"}).status_code == 404


def test_read_escape_404(git_repo):
    client = _client(git_repo, allow=False)
    assert client.get("/api/file", params={"path": "../../etc/passwd"}).status_code == 404


# ---------- 保存：三道闸 + Guard 铁轨 ----------


def test_save_readonly_403(git_repo):
    client = _client(git_repo, allow=False)
    resp = client.post("/api/file", json={"path": "src/x.md", "content": "hi", "by": "lay"})
    assert resp.status_code == 403
    assert "只读" in resp.json()["detail"]


def test_save_requires_token_and_signature(git_repo):
    client, token = _writable(git_repo)
    assert _save(client, "", "src/x.md", "hi").status_code == 403
    assert (
        client.post(
            "/api/file",
            json={"path": "src/x.md", "content": "hi", "by": " "},
            headers={"X-Tram-Token": token},
        ).status_code
        == 422
    )


def test_save_within_baseline_writes_and_records(git_repo):
    client, token = _writable(git_repo)
    resp = _save(client, token, "src/notes.md", "# 车厢笔记\n")
    assert resp.status_code == 200
    assert (git_repo / "src" / "notes.md").read_text(encoding="utf-8") == "# 车厢笔记\n"

    ctx = load_context(git_repo)
    events = [e for e in ctx.events.read() if e.kind == EventKind.FILE_SAVED]
    assert len(events) == 1
    assert events[0].source == "tram.ui"
    assert events[0].data == {"path": "src/notes.md", "bytes": 15, "by": "lay"}


def test_save_outside_baseline_blocked_and_cr_drafted(git_repo):
    """UI 的人工编辑与 agent 修改同一条 Guard 铁轨：越界拦截 + 自动立案。"""
    client, token = _writable(git_repo)
    resp = _save(client, token, "vendor/pyproject.toml", "a=1\n")
    assert resp.status_code == 403
    assert "CR" in resp.json()["detail"]
    assert not (git_repo / "vendor").exists()  # 越界内容不落盘

    ctx = load_context(git_repo)
    crs = CRStore(ctx.crs_dir).open_crs()
    assert len(crs) == 1
    assert crs[0].type.value == "procurement"  # 依赖清单越界同款分类
    kinds = [e.kind for e in ctx.events.read()]
    assert EventKind.INTENT_BLOCKED in kinds and EventKind.CR_CREATED in kinds


def test_save_protected_paths_rejected(git_repo):
    client, token = _writable(git_repo)
    for path in (".tram/state.yaml", ".git/config", ".git/hooks/pre-commit"):
        resp = _save(client, token, path, "tampered")
        assert resp.status_code == 403
        assert "结构保护" in resp.json()["detail"]
    assert not (git_repo / ".tram" / "state.yaml").exists()


def test_save_after_baseline_extension_succeeds(git_repo):
    """立案 → 扩基线（调度台基线编辑器）→ 重存成功：铁轨闭环。"""
    client, token = _writable(git_repo)
    assert _save(client, token, "docs/plan.md", "x").status_code == 403

    import yaml

    base = client.get("/api/baseline").json()
    data = yaml.safe_load(base["yaml"])
    data["allowed_paths"] = sorted(set(data["allowed_paths"]) | {"docs/**"})
    saved = client.post(
        "/api/action",
        json={"verb": "baseline.save", "args": {"yaml": yaml.safe_dump(data)}, "by": "lay"},
        headers={"X-Tram-Token": token},
    )
    assert saved.status_code == 200
    assert _save(client, token, "docs/plan.md", "# 重存\n").status_code == 200
