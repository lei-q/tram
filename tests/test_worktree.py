import subprocess
from pathlib import Path

from tram.sandbox.worktree import WorktreeSession


def _branch_exists(repo: Path, branch: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def test_session_commit_flow(git_repo: Path):
    session = WorktreeSession(git_repo, "T-001")
    with session as worktree:
        (worktree / "new.py").write_text("x = 1\n", encoding="utf-8")
        (worktree / "README.md").write_text("changed\n", encoding="utf-8")
        pending = session.pending_changes()
        assert "new.py" in pending and "README.md" in pending
        sha = session.commit_all("tram T-001: test commit")
        assert sha and session.committed
        assert session.pending_changes() == []
    assert not worktree.exists()  # cleaned up after commit
    assert _branch_exists(git_repo, "tram/t-001")
    assert session.kept is False


def test_session_keeps_uncommitted_work(git_repo: Path):
    session = WorktreeSession(git_repo, "T-002")
    with session as worktree:
        (worktree / "risky.py").write_text("boom\n", encoding="utf-8")
    assert worktree.exists()  # never destroy unreviewed work
    assert session.kept is True
    # cleanup for the next test run environments
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=git_repo,
        capture_output=True,
    )


def test_slugify_handles_messy_ids():
    from tram.sandbox.worktree import slugify

    assert slugify("T-003 Fix: payment 模块!") == "t-003-fix-payment"
