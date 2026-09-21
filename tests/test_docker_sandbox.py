"""Docker sandbox: agent argv runs in a container; governance stays host-side."""

from __future__ import annotations

import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from tram.adapters.base import RunResult
from tram.cli import app
from tram.models.task import TaskSpec
from tram.sandbox.docker import DockerSandboxRunner, docker_available

runner_cli = CliRunner()


class BusyboxRunner:
    """Minimal inner runner: build_cmd writes a file inside the container."""

    name = "busybox"

    def build_cmd(self, task: TaskSpec) -> list[str]:
        return [
            "sh",
            "-c",
            f"echo {task.prompt} > made-in-container.txt",
        ]

    def parse(self, proc: subprocess.CompletedProcess) -> RunResult:
        return RunResult(
            task_id="",
            runner=self.name,
            status="ok" if proc.returncode == 0 else "error",
            summary=proc.stdout[:200],
        )


def _docker_ready() -> bool:
    return shutil.which("docker") is not None and docker_available()


@pytest.mark.skipif(not _docker_ready(), reason="docker daemon not available")
def test_docker_sandbox_writes_reach_host_workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    spec = TaskSpec(id="T-001", prompt="hello-from-container", allowed_tools=[], max_turns=0)
    result = DockerSandboxRunner(BusyboxRunner(), image="busybox:latest", workdir="/workspace").run(
        spec, workspace
    )
    assert result.status == "ok", result.summary
    assert result.runner == "docker(busybox)"
    assert (workspace / "made-in-container.txt").read_text().strip() == "hello-from-container"


@pytest.mark.skipif(not _docker_ready(), reason="docker daemon not available")
def test_docker_sandbox_missing_image_fails_loud(tmp_path):
    """--pull never: 不会偷偷下载镜像，镜像缺失要当场报错。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    spec = TaskSpec(id="T-001", prompt="x", allowed_tools=[], max_turns=0)
    result = DockerSandboxRunner(
        BusyboxRunner(), image="tram-no-such-image:never", workdir="/workspace"
    ).run(spec, workspace)
    assert result.status == "error"


def test_cli_rejects_docker_with_fake_runner(ctx, git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    result = runner_cli.invoke(
        app,
        ["agent", "run", "--prompt", "x", "--runner", "fake", "--sandbox", "docker"],
    )
    assert result.exit_code == 1
    assert "--runner claude" in result.output


def test_cli_rejects_unknown_sandbox(ctx, git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    result = runner_cli.invoke(
        app,
        ["agent", "run", "--prompt", "x", "--runner", "fake", "--sandbox", "vagrant"],
    )
    assert result.exit_code == 1
    assert "unknown sandbox" in result.output


def test_cli_docker_config_roundtrip(ctx, git_repo):
    """docker_image 可经 tram.yaml 配置（引擎 CLI 装在自定义镜像里）。"""
    from tram.config import TramConfig

    assert ctx.config.docker_image
    config_file = ctx.tram_dir / "tram.yaml"
    ctx.config.docker_image = "registry.internal/agent:latest"
    ctx.config.dump(config_file)
    reloaded = TramConfig.load(config_file)
    assert reloaded.docker_image == "registry.internal/agent:latest"
