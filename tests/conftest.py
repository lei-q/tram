from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=tram@test", "-c", "user.name=tram", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _venv_bin_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `python3` subprocesses resolve to the test environment's python."""
    monkeypatch.setenv(
        "PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    )


@pytest.fixture
def git():
    return _git


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


@pytest.fixture
def ctx(git_repo: Path):
    from tram.context import init_project

    return init_project(git_repo, name="demo")
