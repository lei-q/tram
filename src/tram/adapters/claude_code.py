"""Claude Code adapter - headless CLI, stream-json tool trace into the black box."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tram.adapters.base import RunnerUnavailableError, RunResult
from tram.models.task import TaskSpec


class ClaudeCodeRunner:
    name = "claude-code"

    def __init__(self, binary: str = "claude") -> None:
        self.binary = binary

    def build_cmd(self, task: TaskSpec) -> list[str]:
        """The engine argv, workspace-relative. Docker 沙箱靠它翻译成容器命令。"""
        cmd = [
            self.binary,
            "-p",
            task.prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if task.allowed_tools:
            cmd += ["--allowedTools", ",".join(task.allowed_tools)]
        if task.max_turns:
            cmd += ["--max-turns", str(task.max_turns)]
        return cmd

    def parse(self, proc: subprocess.CompletedProcess) -> RunResult:
        """stream-json -> RunResult（工具轨迹进黑匣子）。"""
        trace: list[dict] = []
        final: dict = {}
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = obj.get("type")
            if kind == "assistant":
                for block in obj.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        trace.append(
                            {
                                "type": "tool_use",
                                "name": block.get("name"),
                                "input": block.get("input"),
                            }
                        )
            elif kind == "result":
                final = obj

        status = "ok" if final.get("subtype") == "success" else "error"
        summary = str(final.get("result") or final.get("subtype") or f"exit={proc.returncode}")
        return RunResult(
            task_id="",  # filled by run()
            runner=self.name,
            status=status,
            summary=summary[:2000],
            tool_trace=trace,
        )

    def run(self, task: TaskSpec, workspace: Path) -> RunResult:
        argv = self.build_cmd(task)
        try:
            proc = subprocess.run(
                argv,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=task.timeout_s,
            )
        except FileNotFoundError as exc:
            raise RunnerUnavailableError(
                f"'{self.binary}' CLI not found; install Claude Code or use --runner fake"
            ) from exc
        result = self.parse(proc)
        result.task_id = task.id
        return result
