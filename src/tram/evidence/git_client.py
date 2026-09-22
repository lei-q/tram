"""Read-only git evidence client - commits, diffs, pending changes."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


class GitClient:
    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def run(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc

    def is_repo(self) -> bool:
        return self.run("rev-parse", "--is-inside-work-tree", check=False).returncode == 0

    def current_sha(self) -> str | None:
        proc = self.run("rev-parse", "HEAD", check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def commit_exists(self, sha: str) -> bool:
        proc = self.run("cat-file", "-e", f"{sha}^{{commit}}", check=False)
        return proc.returncode == 0

    def changed_files(self, base: str, head: str = "HEAD") -> list[str]:
        proc = self.run("diff", "--name-only", base, head)
        return [p for p in proc.stdout.splitlines() if p.strip()]

    def untracked_files(self) -> list[str]:
        proc = self.run("ls-files", "--others", "--exclude-standard")
        return [p for p in proc.stdout.splitlines() if p.strip()]

    def pending_changes(self) -> list[str]:
        """Changed + staged + untracked files vs HEAD, in this repo."""
        proc = self.run("status", "--porcelain", "-uall")
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
