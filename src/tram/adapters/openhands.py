"""OpenHands adapter - headless CLI，JSONL 事件流翻译成统一事件词汇.

OpenHands CLI（pypi 包 ``openhands``，入口 ``openhands``）headless 模式
``openhands --headless --json -t "<task>"``：stdout 逐行吐 SDK Event 的
``model_dump()``，每个事件带 ``kind``=类名（MessageEvent / ActionEvent /
ObservationEvent / AgentErrorEvent / ConversationErrorEvent /
ConversationStateUpdateEvent …）。本适配器把 OpenHands 词汇翻译成与
claude 适配器同一套事件词汇（assistant / result / error），下游
``fold_stream`` 与会话车厢 ``ui_line`` 零改动。

会话句柄是 conversation id：从 ``ConversationStateUpdateEvent(key="id")``
里捡；续聊 ``--resume <id>``。新建会话 CLI 不接受预置 id——与 claude
的 --session-id 不同，这里只能等流里报回来。
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


class OpenHandsRunner:
    name = "openhands"

    def __init__(self, binary: str = "openhands") -> None:
        self.binary = binary

    def build_cmd(self, task: TaskSpec) -> list[str]:
        """The engine argv, workspace-relative. Docker 沙箱靠它翻译成容器命令。"""
        cmd = [self.binary, "--headless", "--json"]
        if task.resume and task.session_id:
            cmd += ["--resume", task.session_id]
        cmd += ["-t", task.prompt]
        return cmd

    @staticmethod
    def consume(events: Iterable[dict[str, Any]]) -> RunResult:
        """统一事件词汇 -> RunResult（与 claude 适配器共用同一套折叠）。"""
        return fold_stream(events, OpenHandsRunner.name)

    def stream(self, task: TaskSpec, workspace: Path) -> Iterator[dict[str, Any]]:
        """跑引擎，OpenHands JSONL 逐事件翻译成统一词汇后 yield。"""
        binary = resolve_binary(self.binary)
        if binary is None:
            raise RunnerUnavailableError(
                f"'{self.binary}' CLI not found——PATH 与常见安装位"
                "（~/.local/bin、/opt/homebrew/bin、/usr/local/bin）都没有；"
                "pip install openhands 后重试，或换个引擎"
            )
        argv = self.build_cmd(task)
        argv[0] = binary
        # 压掉 banner / 遥测：stdout 是 JSONL 轨道，人类可读噪音越少越好
        env = {
            **spawn_env(),
            "OPENHANDS_SUPPRESS_BANNER": "1",
            "OPENHANDS_DISABLE_ANALYTICS": "1",
        }
        try:
            proc = subprocess.Popen(
                argv,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RunnerUnavailableError(
                f"'{self.binary}' CLI not found; install openhands (pip install openhands) "
                "or use another engine"
            ) from exc
        assert proc.stdout is not None and proc.stderr is not None

        # stderr 必须持续排干，否则缓冲写满会卡死子进程
        threading.Thread(target=proc.stderr.read, daemon=True).start()
        conv_id: str | None = None
        agent_text = ""
        saw_error = False
        for line in proc.stdout:
            obj = _parse_json(line)
            if obj is None:
                continue
            event, conv_id, agent_text, saw_error = _translate(obj, conv_id, agent_text, saw_error)
            if event is not None:
                yield event
        proc.wait()
        if proc.returncode != 0:
            saw_error = True
        # OpenHands 没有 result 事件——进程退出即终局，这里合成统一 result
        yield {
            "type": "result",
            "subtype": "error" if saw_error else "success",
            "session_id": conv_id,
            "result": agent_text or ("run failed" if saw_error else "done"),
        }

    def run(self, task: TaskSpec, workspace: Path) -> RunResult:
        result = self.consume(self.stream(task, workspace))
        result.task_id = task.id
        return result


def _translate(
    obj: dict[str, Any], conv_id: str | None, agent_text: str, saw_error: bool
) -> tuple[dict[str, Any] | None, str | None, str, bool]:
    """一条 OpenHands 事件 -> (统一事件, 会话句柄, 最终文本, 出错标记)。"""
    kind = obj.get("kind")
    if kind == "ConversationStateUpdateEvent" and obj.get("key") == "id":
        return None, str(obj.get("value") or "") or None, agent_text, saw_error
    if kind == "MessageEvent" and obj.get("source") == "agent":
        text = _message_text(obj)
        if not text:
            return None, conv_id, agent_text, saw_error
        return (
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": text}]},
            },
            conv_id,
            text,
            saw_error,
        )
    if kind == "ActionEvent":
        return (
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": obj.get("tool_name"),
                            "input": obj.get("action"),
                        }
                    ]
                },
            },
            conv_id,
            agent_text,
            saw_error,
        )
    if kind == "AgentErrorEvent":
        # 工具级错误：会话可以继续，亮一行错误但不改终局
        return (
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": f"⚠ {obj.get('error', 'agent error')}"}]
                },
            },
            conv_id,
            agent_text,
            saw_error,
        )
    if kind == "ConversationErrorEvent":
        # 会话级失败：终局 error
        return (
            {"type": "error", "exit_code": str(obj.get("code", "conversation error"))},
            conv_id,
            agent_text,
            True,
        )
    return None, conv_id, agent_text, saw_error


def _message_text(obj: dict[str, Any]) -> str:
    blocks = obj.get("llm_message", {}).get("content", [])
    return "".join(
        str(b.get("text") or "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
    ).strip()


def _parse_json(line: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
