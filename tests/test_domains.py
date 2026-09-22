"""十大知识域归属：确定性分类器 + 文件车厢的域标注与平铺视图."""

import pytest

from tram import operations
from tram.context import load_context
from tram.governance.domains import SUBPROCESSES, classify_path, domains_payload


@pytest.fixture(autouse=True)
def _initialized(ctx):
    """operations 文件服务需要 .tram/ 已初始化."""


def test_classify_path_covers_all_areas():
    assert classify_path(".tram/events.jsonl")["area"] == "整合"  # 证据 → 管理项目知识
    assert classify_path("pyproject.toml")["area"] == "采购"  # 依赖清单 → 实施采购
    assert classify_path("tests/test_chat.py")["area"] == "质量"
    assert classify_path("tests/test_chat.py")["process"] == "管理质量"
    assert classify_path(".github/workflows/ci.yml")["process"] == "控制质量"  # CI 在监控组
    assert classify_path(".github/workflows/ci.yml")["group"] == "监控"
    assert classify_path("docs/PLAN.md")["area"] == "沟通"
    assert classify_path("scripts/build.sh")["area"] == "资源"
    assert classify_path("src/main.py")["process"] == "指导与管理工作"  # 默认：产品工作
    assert classify_path("src/main.py")["group"] == "执行"


def test_subprocesses_cover_all_ten_areas():
    seen = {proc[0] for procs in SUBPROCESSES.values() for proc in procs}
    payload = domains_payload()
    assert len(payload["areas"]) == 10
    assert len(seen) == 10  # 十大知识域在线路图上全部出现


def test_file_list_entries_carry_domain(git_repo):
    (git_repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (git_repo / "README.md").write_text("hi\n", encoding="utf-8")
    (git_repo / "src").mkdir()
    ctx = load_context(git_repo)
    entries = operations.file_list(ctx, "")
    by_name = {e["name"]: e for e in entries}
    assert by_name["pyproject.toml"]["domain"]["area"] == "采购"
    assert by_name["README.md"]["domain"]["area"] == "沟通"
    assert "domain" not in by_name["src"]  # 目录是容器，不带域


def test_file_flat_skips_git_and_lists_all_files(git_repo):
    (git_repo / "src").mkdir()
    (git_repo / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    ctx = load_context(git_repo)
    flat = operations.file_flat(ctx)
    paths = [e["path"] for e in flat]
    assert "src/app.py" in paths
    assert all(not p.startswith(".git/") for p in paths)
    assert all(e["type"] == "file" and "domain" in e for e in flat)
