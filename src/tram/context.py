"""TramContext - one object wiring repo, config, state, events, git, policies."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from tram.config import TramConfig
from tram.cr_store import ScopeBaseline
from tram.evidence.git_client import GitClient
from tram.governance.policy_engine import make_policy_engine
from tram.models.events import EventKind
from tram.models.state import ProjectState
from tram.obs.eventlog import EventLog
from tram.state_store import StateStore

TRAM_DIR = ".tram"
PACKAGED_POLICIES = Path(__file__).parent / "governance" / "policies"
PACKAGED_ROLES = Path(__file__).parent / "roles"
ROLE_KINDS = ("pm", "qa", "dev")

BASELINE_TEMPLATE = """\
# 范围基线（Intent Guard 依据）：列出本任务允许改动的路径 globs。
# 改动不在 allowed_paths 内（或命中 forbidden_paths）会被拦截并生成变更请求（CR）。
version: 1
allowed_paths:
  - "src/**"
  - "tests/**"
  - "*.py"
  - "pyproject.toml"
  - "README.md"
  - ".gitignore"
forbidden_paths: []
approved_by: null
approved_at: null
"""


class NotInitializedError(RuntimeError):
    pass


@dataclass
class TramContext:
    repo: Path
    config: TramConfig
    state_store: StateStore
    events: EventLog
    git: GitClient

    @property
    def tram_dir(self) -> Path:
        return self.repo / TRAM_DIR

    @property
    def gates_file(self) -> Path:
        return self.tram_dir / "gates.yaml"

    @property
    def artifacts_index(self) -> Path:
        return self.tram_dir / "artifacts" / "index.json"

    @property
    def crs_dir(self) -> Path:
        return self.tram_dir / "crs"

    @property
    def baseline_file(self) -> Path:
        return self.tram_dir / "scope-baseline.yaml"

    @property
    def wbs_file(self) -> Path:
        return self.tram_dir / "wbs.yaml"

    def load_wbs(self) -> list[dict]:
        """WBS 工作包（规划工件，PM 手写）：[{id, title, area?, paths: [glob]}].

        空文件/缺文件 = 自由模式（无锚定校验，并线时提示）。schema 坏了
        直接抛错——规划工件不能静默降级。
        """
        if not self.wbs_file.exists():
            return []
        doc = yaml.safe_load(self.wbs_file.read_text(encoding="utf-8")) or {}
        packages = doc.get("packages") or []
        if not isinstance(packages, list):
            raise ValueError("wbs.yaml: 'packages' 必须是列表")
        seen: set[str] = set()
        for pkg in packages:
            pid = pkg.get("id")
            if not pid or pid in seen:
                raise ValueError(f"wbs.yaml: 工作包 id 缺失或重复（{pid}）")
            seen.add(pid)
            if not pkg.get("paths"):
                raise ValueError(f"wbs.yaml: 工作包 {pid} 缺 paths（交付物路径 glob）")
        return packages

    @property
    def packaged_policies(self) -> Path:
        return PACKAGED_POLICIES

    @property
    def roles_dir(self) -> Path:
        return self.tram_dir / "roles"

    def role_prompt_file(self, role: str) -> Path:
        """项目自定义角色模板优先；未定制则回落到包内模板。"""
        custom = self.roles_dir / f"{role}.md"
        return custom if custom.exists() else PACKAGED_ROLES / f"{role}.md"

    def make_policy_engine(self):
        return make_policy_engine(self.config, self.repo, PACKAGED_POLICIES)

    def load_baseline(self) -> ScopeBaseline:
        return ScopeBaseline.load(self.baseline_file)

    def load_state(self) -> ProjectState:
        return self.state_store.load()


def _ensure_gitignore(repo: Path) -> None:
    gitignore = repo / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if ".tram/" not in content.splitlines():
            content = content.rstrip("\n") + "\n.tram/\n"
            gitignore.write_text(content, encoding="utf-8")
    else:
        gitignore.write_text(".tram/\n", encoding="utf-8")


def init_project(repo: Path, name: str | None = None, force: bool = False) -> TramContext:
    tram_dir = repo / TRAM_DIR
    if tram_dir.exists() and not force:
        raise NotInitializedError(f"{tram_dir} already exists (use --force to reinitialize)")
    if not (repo / ".git").exists():
        raise NotInitializedError(f"{repo} is not a git repository (git init first)")

    (tram_dir / "events").mkdir(parents=True, exist_ok=True)
    (tram_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (tram_dir / "crs").mkdir(parents=True, exist_ok=True)
    (tram_dir / "worktrees").mkdir(parents=True, exist_ok=True)
    (tram_dir / "roles").mkdir(parents=True, exist_ok=True)
    for role in ROLE_KINDS:
        role_file = tram_dir / "roles" / f"{role}.md"
        if not role_file.exists():
            shutil.copyfile(PACKAGED_ROLES / f"{role}.md", role_file)

    config = TramConfig(project_name=name or repo.name)
    config.dump(tram_dir / "tram.yaml")
    shutil.copyfile(PACKAGED_POLICIES / "gates.yaml", tram_dir / "gates.yaml")
    (tram_dir / "artifacts" / "index.json").write_text("[]", encoding="utf-8")
    baseline_file = tram_dir / "scope-baseline.yaml"
    if not baseline_file.exists():
        baseline_file.write_text(BASELINE_TEMPLATE, encoding="utf-8")
    _ensure_gitignore(repo)

    git = GitClient(repo)
    state_store = StateStore(tram_dir / "state.json")
    state_store.save(ProjectState(project_name=config.project_name))
    events = EventLog(tram_dir / "events" / "events.jsonl")
    refs = {}
    sha = git.current_sha()
    if sha:
        refs["commit"] = sha
    events.append(
        EventKind.PROJECT_INITIALIZED,
        source="tram.init",
        data={"project_name": config.project_name, "sandbox": config.sandbox.value},
        refs=refs,
    )
    return TramContext(repo=repo, config=config, state_store=state_store, events=events, git=git)


def load_context(repo: Path | None = None) -> TramContext:
    repo = (repo or Path.cwd()).resolve()
    config_file = repo / TRAM_DIR / "tram.yaml"
    if not config_file.exists():
        raise NotInitializedError(f"{repo} has no .tram/ - run `tram init` first")
    config = TramConfig.load(config_file)
    return TramContext(
        repo=repo,
        config=config,
        state_store=StateStore(repo / TRAM_DIR / "state.json"),
        events=EventLog(repo / TRAM_DIR / "events" / "events.jsonl"),
        git=GitClient(repo),
    )
