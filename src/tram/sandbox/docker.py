"""Docker sandbox (optional): agent 命令进容器，治理留在宿主机。

R3 的答案是容器隔离：worktree 提供变更隔离，docker 提供执行隔离——
Intent Guard、提交、事件流全部还在宿主机上确定性执行，容器只跑引擎。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from tram.adapters.base import RunnerUnavailableError, RunResult
from tram.models.task import TaskSpec

DEFAULT_IMAGE = "node:22-bookworm-slim"  # 需镜像内已装好引擎 CLI（如 claude）
DEFAULT_WORKDIR = "/workspace"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


class DockerSandboxRunner:
    """把 inner runner 的 argv 翻译成 `docker run`，输出解析复用 inner。"""

    def __init__(
        self,
        inner,
        image: str = DEFAULT_IMAGE,
        workdir: str = DEFAULT_WORKDIR,
    ) -> None:
        self.inner = inner
        self.image = image
        self.workdir = workdir
        self.name = f"docker({inner.name})"

    def run(self, task: TaskSpec, workspace: Path) -> RunResult:
        if not docker_available():
            raise RunnerUnavailableError(
                "docker daemon unavailable; start Docker or use --sandbox worktree"
            )
        argv = self.inner.build_cmd(task)
        docker_argv = [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",  # 离线优先：镜像不存在直接报错，不偷偷下载
            "-v",
            f"{workspace.resolve()}:{self.workdir}",
            "-w",
            self.workdir,
            self.image,
            *argv,
        ]
        try:
            proc = subprocess.run(
                docker_argv,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=task.timeout_s,
            )
        except FileNotFoundError as exc:
            raise RunnerUnavailableError("docker CLI not found") from exc
        result = self.inner.parse(proc)
        result.task_id = task.id
        result.runner = self.name
        return result
