"""Intent Guard - changed paths must stay inside the approved scope baseline."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from tram.cr_store import ScopeBaseline
from tram.models.cr import CRType


def compile_glob(pattern: str) -> re.Pattern:
    """Translate a path glob (supports ``**``) into an anchored regex."""
    pat = pattern.strip()
    if pat.startswith("./"):  # keep dotfiles: only strip a literal ./ prefix
        pat = pat[2:]
    out: list[str] = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("".join(out) + "$")


def path_matches(path: str, patterns: list[str]) -> bool:
    return any(compile_glob(p).match(path) for p in patterns)


# 采购（lite 域）= 依赖引入：这些清单文件被越界改动时，CR 按 procurement 建档
# （**/ 前缀 = 任意深度，monorepo 子包同样命中；零层目录时退化为根路径本身）
PROCUREMENT_PATTERNS: list[str] = [
    "**/pyproject.toml",
    "**/requirements*.txt",
    "**/poetry.lock",
    "**/uv.lock",
    "**/Pipfile",
    "**/Pipfile.lock",
    "**/package.json",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/Cargo.toml",
    "**/Cargo.lock",
    "**/go.mod",
    "**/go.sum",
    "**/pom.xml",
    "**/build.gradle",
    "**/build.gradle.kts",
    "**/Gemfile",
    "**/Gemfile.lock",
    "**/composer.json",
]


def classify_violations(paths: list[str]) -> CRType:
    """确定性分类：任一越界路径命中依赖清单 -> procurement，否则 scope。"""
    if any(path_matches(p, PROCUREMENT_PATTERNS) for p in paths):
        return CRType.PROCUREMENT
    return CRType.SCOPE


class GuardDecision(BaseModel):
    ok: bool
    violations: list[str] = Field(default_factory=list)
    allowed: list[str] = Field(default_factory=list)


class IntentGuard:
    def __init__(self, baseline: ScopeBaseline) -> None:
        self.baseline = baseline

    def check(self, changed_paths: list[str]) -> GuardDecision:
        violations: list[str] = []
        allowed: list[str] = []
        for path in sorted(set(changed_paths)):
            in_allowed = path_matches(path, self.baseline.allowed_paths)
            in_forbidden = path_matches(path, self.baseline.forbidden_paths)
            if in_forbidden or not in_allowed:
                violations.append(path)
            else:
                allowed.append(path)
        return GuardDecision(ok=not violations, violations=violations, allowed=allowed)
