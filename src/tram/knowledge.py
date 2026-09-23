"""护航知识库 - 对话与变动持续沉淀为项目文件，下轮对话按需入上下文.

护航者定位（2026-09-23 纲领）：Tram 不驾驶列车，只收集行驶数据、
预警风险、纠偏脱轨行为。知识库是"给驾驶员的航行资料"：
- 确定性收集：每轮跑完刷新监控组文件（变更日志/进度报告），事实抄录
- 引擎提炼：收车或手动触发，把对话内容蒸馏成各过程组项目文件
  （建议书/章程/管理计划/基准/风险登记册…）——LLM 只生成内容，
  文件名白名单与落盘位置由 Tram 控制，判定路径无 LLM
- 按需加载：下轮对话前导拼知识库摘要（各文件截断节选，总量封顶）
"""

from __future__ import annotations

import re

from tram.context import TramContext
from tram.models.events import EventKind
from tram.models.task import TaskStatus

KNOWLEDGE_DIR = ".tram/knowledge"
DOCS_DIR = f"{KNOWLEDGE_DIR}/docs"

# 项目文件白名单：过程组 -> 文件（提炼器只能写这些名字，防越权落盘）
DOC_GROUPS: dict[str, list[str]] = {
    "01-initiating": ["项目建议书.md", "项目章程.md"],
    "02-planning": [
        "项目管理计划.md",
        "范围管理计划.md",
        "进度管理计划.md",
        "成本管理计划.md",
        "范围基准.md",
        "进度基准.md",
        "成本基准.md",
        "风险登记册.md",
    ],
    "04-monitoring": ["变更日志.md", "进度报告.md"],
}
DOC_GROUP_OF = {name: group for group, names in DOC_GROUPS.items() for name in names}

MAX_DOC_CHARS = 20000
FILE_MARK = re.compile(r"^===\s*FILE:\s*(.+?)\s*===\s*$")


# ---------- 确定性收集（每轮跑完，事实抄录） ----------


def refresh_monitoring(ctx: TramContext) -> list[str]:
    """刷新监控组两份文件：变更日志（事件流尾部）+ 进度报告（任务与 EVM）."""
    from pathlib import Path

    state = ctx.load_state()
    written: list[str] = []

    changes = Path(ctx.repo) / KNOWLEDGE_DIR / "changes.md"
    tail = ""
    if changes.exists():
        lines = changes.read_text(encoding="utf-8").splitlines()
        tail = "\n".join(lines[-25:])
    _write_doc(
        ctx,
        "变更日志.md",
        _doc_header("变更日志")
        + "\n最近变更（事实抄录，来源 .tram/knowledge/changes.md）：\n\n"
        + (tail or "（暂无）\n"),
    )
    written.append("变更日志.md")

    from tram.metrics.evm import latest_snapshot

    done = sum(1 for t in state.tasks if t.status == TaskStatus.DONE)
    doing = sum(1 for t in state.tasks if t.status == TaskStatus.DOING)
    snap = latest_snapshot(ctx)
    evm = (
        f"SPI {snap.spi} · CPI {snap.cpi}（{snap.date}）"
        if snap
        else "（尚无快照，tram monitor / 📊 EVM）"
    )
    open_risks = [r for r in state.risks if r.status != "closed"]
    body = (
        f"# 进度报告\n\n- 阶段：{state.phase.value} · 环线第 {state.iteration} 圈\n"
        f"- 任务：{len(state.tasks)} 项（{done} 完成 / {doing} 在途）\n"
        f"- EVM：{evm}\n"
        f"- 未决风险：{len(open_risks)} 项；未决 CR：{len(state.open_crs)} 条\n"
    )
    _write_doc(ctx, "进度报告.md", body)
    written.append("进度报告.md")
    return written


# ---------- 引擎提炼（收车 / 手动触发） ----------


def distill(ctx: TramContext, engine_name: str, by: str) -> dict:
    """把对话与变更蒸馏进项目文件：LLM 只产内容，白名单与落盘由 Tram 控制."""
    from tram.adapters.base import RunnerUnavailableError
    from tram.models.task import TaskSpec

    drafts = _collect_drafts(ctx)
    context = _collect_context(ctx)
    prompt = _distill_prompt(drafts, context)
    engine = _make_engine(engine_name)
    spec = TaskSpec(id="KNOWLEDGE", prompt=prompt)

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = engine.run(spec, _path(tmp))
        except RunnerUnavailableError as exc:
            return {"files": [], "skipped": str(exc)}

    written = _apply_distillation(ctx, result.summary or "")
    ctx.events.append(
        EventKind.KNOWLEDGE_DISTILLED,
        source="tram.knowledge",
        data={"engine": engine_name, "files": written, "by": by},
    )
    return {"files": written}


def _distill_prompt(drafts: dict[str, str], context: str) -> str:
    allow = "\n".join(f"- {name}" for name in DOC_GROUP_OF)
    draft_text = (
        "\n\n".join(f"=== FILE: {name} ===\n{text[:800]}" for name, text in drafts.items())
        or "（还没有任何项目文件草稿）"
    )
    return (
        "你是 Tram 护航体系的知识官：把下面的对话记录与项目现状，蒸馏成/更新为项目管理文件。\n\n"
        f"【允许输出的文件（白名单，此外一律忽略）】\n{allow}\n\n"
        "【现有文件草稿（截断）】\n" + draft_text + "\n\n"
        "【对话与变更素材】\n" + (context or "（暂无）") + "\n\n"
        "【输出格式】每个文件一段：先一行 `=== FILE: 文件名 ===`，接 markdown 正文（保留草稿中\n"
        "仍然成立的内容，增量合并新信息，不要凭空编造事实）。没新材料可更新的文件不要输出。\n"
    )


def _collect_context(ctx: TramContext) -> str:
    """最近对话 + 变更/风险账尾部——提炼器的原料（只读，截断防刷爆）."""
    from pathlib import Path

    chunks: list[str] = []
    chat_dir = Path(ctx.repo) / ".tram" / "chat"
    logs = sorted(chat_dir.glob("chat-*/log.jsonl"))[-3:]
    convo: list[str] = []
    for log in logs:
        try:
            lines = log.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines[-20:]:
            try:
                import json

                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            text = str(obj.get("text") or obj.get("input") or "")[:160]
            if text:
                convo.append(f"[{obj.get('k', '?')}] {text}")
    if convo:
        chunks.append("最近对话：\n" + "\n".join(convo[-40:]))
    for ledger, label in (("changes.md", "最近变更"), ("risks.md", "最近风险")):
        p = Path(ctx.repo) / KNOWLEDGE_DIR / ledger
        if p.exists():
            tail = "\n".join(p.read_text(encoding="utf-8").splitlines()[-15:])
            if tail.strip():
                chunks.append(f"{label}：\n{tail}")
    return "\n\n".join(chunks)[:8000]


def _apply_distillation(ctx: TramContext, text: str) -> list[str]:
    written: list[str] = []
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = FILE_MARK.match(line)
        if m:
            if current in DOC_GROUP_OF and buf:
                _write_doc(ctx, current, "\n".join(buf).strip()[:MAX_DOC_CHARS])
                written.append(current)
            name = m.group(1).strip()
            current = name if name in DOC_GROUP_OF else None  # 白名单外整段丢弃
            buf = []
        elif current is not None:
            buf.append(line)
    if current in DOC_GROUP_OF and buf:
        _write_doc(ctx, current, "\n".join(buf).strip()[:MAX_DOC_CHARS])
        written.append(current)
    return sorted(set(written))


def _collect_drafts(ctx: TramContext) -> dict[str, str]:
    from pathlib import Path

    drafts: dict[str, str] = {}
    for name, group in DOC_GROUP_OF.items():
        p = Path(ctx.repo) / DOCS_DIR / group / name
        if p.exists():
            drafts[name] = p.read_text(encoding="utf-8")
    return drafts


def _write_doc(ctx: TramContext, name: str, content: str) -> None:
    from pathlib import Path

    target = Path(ctx.repo) / DOCS_DIR / DOC_GROUP_OF[name] / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.rstrip() + "\n", encoding="utf-8")


def _doc_header(title: str) -> str:
    return f"# {title}"


def _path(tmp: str):
    from pathlib import Path

    return Path(tmp)


def _make_engine(engine_name: str):
    if engine_name == "fake":
        from tram.adapters.fake import FakeRunner

        return FakeRunner()
    if engine_name == "openhands":
        from tram.adapters.openhands import OpenHandsRunner

        return OpenHandsRunner()
    from tram.adapters.claude_code import ClaudeCodeRunner

    return ClaudeCodeRunner()


# ---------- 按需加载（下轮对话前导的知识库摘要） ----------

DIGEST_SECTIONS = (
    ("项目章程.md", 500),
    ("风险登记册.md", 400),
    ("进度报告.md", 400),
    ("变更日志.md", 300),
)
DIGEST_CAP = 2200


def digest(ctx: TramContext) -> str:
    """知识库摘要：关键文件节选，总量封顶——拼进下轮对话前导."""
    from pathlib import Path

    parts: list[str] = []
    used = 0
    for name, cap in DIGEST_SECTIONS:
        p = Path(ctx.repo) / DOCS_DIR / DOC_GROUP_OF[name] / name
        if not p.exists():
            continue
        excerpt = p.read_text(encoding="utf-8").strip()[:cap]
        if not excerpt:
            continue
        if used + len(excerpt) > DIGEST_CAP:
            excerpt = excerpt[: max(0, DIGEST_CAP - used)]
        if not excerpt:
            continue
        parts.append(f"《{name}》\n{excerpt}")
        used += len(excerpt)
        if used >= DIGEST_CAP:
            break
    return "\n\n".join(parts)
