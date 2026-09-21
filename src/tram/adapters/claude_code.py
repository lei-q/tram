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

    def run(self, task: TaskSpec, workspace: Path) -> RunResult:
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
        try:
            proc = subprocess.run(
                cmd,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=task.timeout_s,
            )
        except FileNotFoundError as exc:
            raise RunnerUnavailableError(
                f"'{self.binary}' CLI not found; install Claude Code or use --runner fake"
            ) from exc

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
            task_id=task.id,
            runner=self.name,
            status=status,
            summary=summary[:2000],
            tool_trace=trace,
        )
