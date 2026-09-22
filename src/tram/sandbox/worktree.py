"""git worktree sandbox (default). Changes land on a tram/<task> branch.

Uncommitted violations are never destroyed: the worktree is kept on exit
when changes exist and were not committed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip().lower()).strip("-.")
    return slug or "task"


# 提交由 Tram 亲自做：作者身份固定为 tram，不依赖环境里的 git 配置
# （CI 或裸机可能没有 user.name/email，git 会直接拒绝提交）。
TRAM_IDENTITY = ("-c", "user.name=tram", "-c", "user.email=tram@localhost")


class WorktreeSession:
    def __init__(
        self,
        repo: Path,
        task_id: str,
        base_ref: str = "HEAD",
        worktrees_dir: Path | None = None,
    ) -> None:
        self.repo = repo
        self.task_id = task_id
        self.base_ref = base_ref
        self.slug = slugify(task_id)
        self.branch = f"tram/{self.slug}"
        self.worktrees_dir = worktrees_dir or repo / ".tram" / "worktrees"
        self.path = self.worktrees_dir / self.slug
        self.committed = False
        self.kept = False

    def __enter__(self) -> Path:
        return self.create()

    def create(self) -> Path:
        """建 worktree（会话车厢常驻场景直接调它，不进 with 块）。"""
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["git", "worktree", "add", "-b", self.branch, str(self.path), self.base_ref],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {proc.stderr.strip()}")
        return self.path

    def pending_changes(self) -> list[str]:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=self.path,
            capture_output=True,
            text=True,
            check=True,
        )
        paths: list[str] = []
        for line in proc.stdout.splitlines():
            if len(line) < 4:
                continue
            entry = line[3:]
            if " -> " in entry:
                old, new = entry.split(" -> ", 1)
                paths.extend([old, new])
            else:
                paths.append(entry)
        return sorted(set(paths))

    def commit_all(self, message: str) -> str | None:
        subprocess.run(["git", "add", "-A"], cwd=self.path, capture_output=True, check=True)
        cached = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=self.path,
            capture_output=True,
        )
        if cached.returncode == 0:
            return None  # nothing to commit
        proc = subprocess.run(
            ["git", *TRAM_IDENTITY, "commit", "-m", message],
            cwd=self.path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git commit failed: {proc.stderr.strip()}")
        self.committed = True
        return self.head_sha()

    def head_sha(self) -> str:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.path,
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip()

    def remove(self) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(self.path)],
            cwd=self.repo,
            capture_output=True,
        )
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo, capture_output=True)

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.kept = True  # debugging an exception: keep everything
            return False
        if self.pending_changes() and not self.committed:
            self.kept = True  # never destroy unreviewed work
            return False
        self.remove()
        return False
