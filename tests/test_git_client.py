from pathlib import Path

from tram.evidence.git_client import GitClient


def _commit(repo: Path, git, filename: str) -> None:
    (repo / filename).write_text(f"content of {filename}\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", f"add {filename}")


def test_is_repo_and_current_sha(git_repo: Path):
    client = GitClient(git_repo)
    assert client.is_repo()
    assert client.current_sha() and len(client.current_sha()) == 40


def test_commit_exists(git_repo: Path, git):
    client = GitClient(git_repo)
    sha = client.current_sha()
    assert client.commit_exists(sha)
    assert not client.commit_exists("0" * 40)


def test_changed_files_between_commits(git_repo: Path, git):
    client = GitClient(git_repo)
    base = client.current_sha()
    _commit(git_repo, git, "b.txt")
    assert client.changed_files(base) == ["b.txt"]


def test_pending_changes_includes_untracked(git_repo: Path):
    client = GitClient(git_repo)
    (git_repo / "untracked.txt").write_text("new\n", encoding="utf-8")
    (git_repo / "README.md").write_text("modified\n", encoding="utf-8")
    pending = client.pending_changes()
    assert "untracked.txt" in pending
    assert "README.md" in pending
