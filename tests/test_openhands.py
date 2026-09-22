"""OpenHands 适配器：headless --json 线格式（kind=SDK 类名）翻译成统一事件词汇.

契约样例取自 openhands-sdk 源码的事件字段（MessageEvent/ActionEvent/
ConversationStateUpdateEvent/AgentErrorEvent/ConversationErrorEvent），
用 stub 二进制走真实 Popen 管道，不依赖引擎安装。
"""

import json
import stat

import pytest

from tram.adapters.base import RunnerUnavailableError, RunResult
from tram.adapters.openhands import OpenHandsRunner
from tram.chat import ENGINES, ChatService, ui_line
from tram.context import load_context
from tram.models.task import TaskSpec


@pytest.fixture(autouse=True)
def _initialized(ctx):
    """ChatService 需要 .tram/ 已初始化（与 test_chat 同约定）。"""


# 与 openhands-sdk 序列化同构的样例流（kind=类名，computed_field）
WIRE_LINES = [
    {"kind": "SystemPromptEvent", "source": "agent"},
    {"kind": "ConversationStateUpdateEvent", "key": "id", "value": "conv-77f"},
    {
        "kind": "MessageEvent",
        "source": "user",
        "llm_message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    },
    {
        "kind": "ActionEvent",
        "source": "agent",
        "thought": [],
        "tool_name": "execute_bash",
        "tool_call_id": "call_1",
        "action": {"command": "ls"},
    },
    {
        "kind": "ObservationEvent",
        "source": "environment",
        "observation": {"content": "f1 f2"},
        "action_id": "call_1",
    },
    {
        "kind": "MessageEvent",
        "source": "agent",
        "llm_message": {"role": "assistant", "content": [{"type": "text", "text": "all done"}]},
    },
]


def _stub(tmp_path, lines, exit_code: int = 0):
    """heredoc 回显 JSONL 的 stub 二进制（行写进正文会被 sh 当命令执行）。"""
    stub = tmp_path / "openhands-stub"
    body = (
        "#!/bin/sh\ncat <<'TRAM_EOF'\n"
        + "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in lines)
        + "TRAM_EOF\n"
    )
    if exit_code:
        body += f"exit {exit_code}\n"
    stub.write_text(body, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


def test_build_cmd_headless_and_resume():
    cmd = OpenHandsRunner().build_cmd(TaskSpec(id="T-1", prompt="fix bug"))
    assert cmd[:4] == ["openhands", "--headless", "--json", "-t"]
    assert "--resume" not in cmd

    cmd = OpenHandsRunner().build_cmd(
        TaskSpec(id="T-1", prompt="继续", session_id="conv-77f", resume=True)
    )
    assert "--resume" in cmd and cmd[cmd.index("--resume") + 1] == "conv-77f"


def test_stream_tolerates_non_json_preamble(tmp_path):
    """活体发现（openhands 1.16）：stdout 混有人类可读行——banner、无 LLM 配置的
    拒绝提示、Goodbye、Conversation ID hint。适配器只吃 JSON 行，其余当噪音。"""
    stub = tmp_path / "openhands-stub"
    stub.write_text(
        "#!/bin/sh\ncat <<'TRAM_EOF'\n"
        "OpenHands CLI terminal UI may not work correctly in this environment\n"
        + json.dumps({"kind": "ConversationStateUpdateEvent", "key": "id", "value": "conv-live"})
        + "\n"
        "\x1b[91mHeadless mode requires existing settings.\x1b[0m\n"
        "Conversation ID: 783b54c1e602440c973619e12b44bb3c\n"
        "Goodbye! 👋\n"
        + json.dumps(
            {
                "kind": "MessageEvent",
                "source": "agent",
                "llm_message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            }
        )
        + "\n"
        "TRAM_EOF\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    result = OpenHandsRunner(binary=str(stub)).run(TaskSpec(id="T-1", prompt="p"), tmp_path)
    assert result.status == "ok"
    assert result.session_id == "conv-live"  # JSON 行照常解析，人类行被跳过
    assert result.summary == "ok"


def test_stream_translates_wire_to_unified_events(tmp_path):
    events = list(
        OpenHandsRunner(binary=str(_stub(tmp_path, WIRE_LINES))).stream(
            TaskSpec(id="T-1", prompt="p"), tmp_path
        )
    )
    tools = [
        b
        for e in events
        for b in e.get("message", {}).get("content", [])
        if b.get("type") == "tool_use"
    ]
    texts = [
        b
        for e in events
        for b in e.get("message", {}).get("content", [])
        if b.get("type") == "text"
    ]
    final = events[-1]
    assert final["type"] == "result" and final["subtype"] == "success"
    assert final["session_id"] == "conv-77f"  # 从 state update 里捡到 conversation id
    assert final["result"] == "all done"
    assert tools == [{"type": "tool_use", "name": "execute_bash", "input": {"command": "ls"}}]
    assert [t["text"] for t in texts] == ["all done"]  # user 消息不翻译
    # 翻译后的事件直接喂会话车厢 ui_line，k 语义不变
    lines = []
    for e in events:
        out = ui_line(e)
        lines.extend(out if isinstance(out, list) else [out] if out else [])
    kinds = [ln["k"] for ln in lines]
    assert "tool" in kinds and "text" in kinds and "result" in kinds


def test_consume_folds_stub_stream(tmp_path):
    result: RunResult = OpenHandsRunner(binary=str(_stub(tmp_path, WIRE_LINES))).run(
        TaskSpec(id="T-9", prompt="p"), tmp_path
    )
    assert result.status == "ok"
    assert result.task_id == "T-9"
    assert result.session_id == "conv-77f"
    assert result.tool_trace == [
        {"type": "tool_use", "name": "execute_bash", "input": {"command": "ls"}}
    ]


def test_conversation_error_marks_failure(tmp_path):
    lines = [
        {"kind": "ConversationStateUpdateEvent", "key": "id", "value": "conv-e1"},
        {"kind": "ConversationErrorEvent", "code": "RuntimeError", "detail": "boom"},
    ]
    result = OpenHandsRunner(binary=str(_stub(tmp_path, lines))).run(
        TaskSpec(id="T-1", prompt="p"), tmp_path
    )
    assert result.status == "error"
    assert result.session_id == "conv-e1"  # 错了句柄也留得下，供下次 --resume


def test_nonzero_exit_marks_failure(tmp_path):
    lines = [
        {
            "kind": "MessageEvent",
            "source": "agent",
            "llm_message": {"role": "assistant", "content": [{"type": "text", "text": "half"}]},
        },
    ]
    result = OpenHandsRunner(binary=str(_stub(tmp_path, lines, exit_code=3))).run(
        TaskSpec(id="T-1", prompt="p"), tmp_path
    )
    assert result.status == "error"
    assert result.summary == "half"  # 留住最后一条 agent 文本，status 扛错误信号


def test_missing_binary_raises_unavailable(tmp_path):
    with pytest.raises(RunnerUnavailableError, match="openhands"):
        list(
            OpenHandsRunner(binary="/nonexistent/openhands").stream(
                TaskSpec(id="T-1", prompt="p"), tmp_path
            )
        )


def test_chat_engine_registry_includes_openhands(git_repo):
    assert "openhands" in ENGINES
    svc = ChatService(load_context(git_repo), inline=True)
    assert svc._engine("openhands").name == "openhands"
