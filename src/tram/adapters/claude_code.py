"""Claude Code adapter - headless CLI, stream-json tool trace into the black box.

stream() 逐事件 yield 引擎输出，consume() 把事件流折叠成 RunResult——
会话车厢拿流式，CLI 拿最终结果，同一条语义。会话句柄走 --session-id
（新建）与 --resume（续聊），由 TaskSpec.session_id/resume 驱动。
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from tram.adapters.base import (
    RunnerUnavailableError,
    RunResult,
    fold_stream,
    resolve_binary,
    spawn_env,
)
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
            "--include-partial-messages",  # 思考增量流：会话车厢实时显示思考过程
        ]
        if task.resume and task.session_id:
            cmd += ["--resume", task.session_id]
        elif task.session_id:
            cmd += ["--session-id", task.session_id]
        if task.allowed_tools:
            cmd += ["--allowedTools", ",".join(task.allowed_tools)]
        else:
            # 沙箱即边界：headless 没人可问，claude 默认把写工具全拒（引擎只能干瞪眼）。
            # worktree 本身就是隔离层，放开工具权限；轨内轨外由 Tram 的 Intent Guard
            # 在跑完后判定——引擎的权限系统管交互机器，Tram 管治理。
            cmd += ["--dangerously-skip-permissions"]
        if task.max_turns:
            cmd += ["--max-turns", str(task.max_turns)]
        return cmd

    @staticmethod
    def consume(events: Iterable[dict[str, Any]]) -> RunResult:
        """把 stream-json 事件流折叠成 RunResult（工具轨迹 + 最终结果 + 会话句柄）。"""
        return fold_stream(events, ClaudeCodeRunner.name)

    def parse(self, proc: subprocess.CompletedProcess) -> RunResult:
        """CompletedProcess -> RunResult（docker 沙箱的阻塞路径复用）。"""
        return self.consume((proc.stdout or "").splitlines())

    def stream(self, task: TaskSpec, workspace: Path, stop_event=None) -> Iterator[dict[str, Any]]:
        """跑引擎并逐事件 yield（会话车厢的流式轨道）。"""
        binary = resolve_binary(self.binary)
        if binary is None:
            raise RunnerUnavailableError(
                f"'{self.binary}' CLI not found——PATH 与常见安装位"
                "（~/.local/bin、~/.claude/local、/opt/homebrew/bin、/usr/local/bin）"
                "都没有；安装 Claude Code 后重试，或换个引擎"
            )
        argv = self.build_cmd(task)
        argv[0] = binary  # build_cmd 保留裸名给 docker 沙箱翻译，spawn 时才落绝对路径
        try:
            proc = subprocess.Popen(
                argv,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=spawn_env(),
            )
        except FileNotFoundError as exc:
            raise RunnerUnavailableError(
                f"'{binary}' CLI not found; install Claude Code or use another engine"
            ) from exc
        assert proc.stdout is not None and proc.stderr is not None

        # stderr 必须持续排干，否则缓冲写满会卡死子进程
        threading.Thread(target=proc.stderr.read, daemon=True).start()
        for line in proc.stdout:
            if stop_event is not None and stop_event.is_set():
                proc.terminate()  # 司机急停：杀引擎进程，本轮作废
                break
            obj = _parse_json(line)
            if obj is not None:
                yield obj
        proc.wait()
        if stop_event is not None and stop_event.is_set():
            return
        if proc.returncode != 0:
            yield {"type": "error", "exit_code": proc.returncode}

    def run(self, task: TaskSpec, workspace: Path) -> RunResult:
        result = self.consume(self.stream(task, workspace))
        result.task_id = task.id
        return result


def _parse_json(line: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
