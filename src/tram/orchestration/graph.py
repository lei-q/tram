"""The governance line as a state machine (T1.10 LangGraph wrapper).

站台→门禁→下一站：`tram run` 沿主线连续过门禁，绿灯推进、红灯停车。
确定性优先：图里的每个节点都只调 GateRunner / 纯函数，LLM 不在决策路径上。

安装 langgraph（`pip install 'tram[orchestration]'`）时用 StateGraph 编排；
未安装时由内置解释器按同一套节点与路由函数行走，行为逐分支一致。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict

from tram.models.gates import GateResult, GateStatus
from tram.orchestration.phases import _EXPECTED_PHASE_BEFORE, PHASE_AFTER_GATE

# 门禁 → 它放行后的下一站（与 phases.py 同一张表的正向视图）
_GATE_FOR_PHASE = {phase: gate for gate, phase in _EXPECTED_PHASE_BEFORE.items()}


class FlowState(TypedDict, total=False):
    phase: str
    current_gate: str | None
    evaluated: list[dict]  # {gate_id, status, reasons, remediation_round}
    journey: list[str]  # 人类可读的行车记录
    stop_reason: str | None


Node = Callable[[FlowState], dict]


def make_nodes(runner) -> dict[str, Node]:
    """节点只依赖 GateRunner：判定在门禁与策略引擎，图只负责调度。"""

    def next_gate(state: FlowState) -> dict:
        gate = _GATE_FOR_PHASE.get(state.get("phase", ""), None)
        if gate is None:
            return {"current_gate": None, "stop_reason": "已达终点站，全线走完 🎉"}
        evaluated = state.get("evaluated", [])
        if (
            evaluated
            and evaluated[-1]["status"] == GateStatus.PASS.value
            and evaluated[-1]["gate_id"] == gate
        ):
            # 门禁通过但相位没推进（越序记录在案）：再跑同一个门只会原地打转
            return {
                "current_gate": None,
                "stop_reason": f"门禁 {gate} 已通过但相位未推进（越序记录），停车复核",
            }
        return {"current_gate": gate}

    def evaluate(state: FlowState) -> dict:
        gate_id = state["current_gate"]
        result: GateResult = runner.run(gate_id)
        entry = {
            "gate_id": gate_id,
            "status": result.status.value,
            "reasons": result.decision_reasons,
            "remediation_round": result.remediation_round,
        }
        journey = list(state.get("journey", []))
        if result.status == GateStatus.PASS:
            arrived = PHASE_AFTER_GATE.get(gate_id)
            line = f"🚦 {gate_id}: PASS" + (f" → 到达 {arrived.value}" if arrived else "")
            journey.append(line)
        else:
            journey.append(
                f"🛑 {gate_id}: {result.status.value}（第 {result.remediation_round} 轮整改）"
            )
        return {
            "phase": runner.ctx.load_state().phase.value,
            "evaluated": [*state.get("evaluated", []), entry],
            "journey": journey,
        }

    def advance(state: FlowState) -> dict:
        return {}  # 相位已由 GateRunner 推进；本节点是图上的「驶向下一站」

    def stop(state: FlowState) -> dict:
        last = state["evaluated"][-1]
        reasons = "; ".join(last["reasons"]) or "未通过"
        return {
            "stop_reason": (
                f"红灯停车：{last['gate_id']} {last['status']}"
                f"（第 {last['remediation_round']} 轮整改）— {reasons}"
            )
        }

    return {"next_gate": next_gate, "evaluate": evaluate, "advance": advance, "stop": stop}


def route_after_next_gate(state: FlowState) -> str:
    return "end" if state.get("current_gate") is None else "evaluate"


def route_after_evaluate(state: FlowState) -> str:
    last = state["evaluated"][-1]
    return "advance" if last["status"] == GateStatus.PASS.value else "stop"


def build_langgraph(runner):
    """返回编译好的 LangGraph；未安装 langgraph 时返回 None（回退解释器）。"""
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:
        return None
    nodes = make_nodes(runner)
    graph = StateGraph(FlowState)
    graph.add_node("next_gate", nodes["next_gate"])
    graph.add_node("evaluate", nodes["evaluate"])
    graph.add_node("advance", nodes["advance"])
    graph.add_node("stop", nodes["stop"])
    graph.set_entry_point("next_gate")
    graph.add_conditional_edges(
        "next_gate", route_after_next_gate, {"evaluate": "evaluate", "end": END}
    )
    graph.add_conditional_edges(
        "evaluate", route_after_evaluate, {"advance": "advance", "stop": "stop"}
    )
    graph.add_edge("advance", "next_gate")
    return graph.compile()


def walk_flow(runner, state: FlowState | None = None) -> FlowState:
    """内置解释器：按与 LangGraph 相同的节点/路由行走（零依赖回退）。"""
    nodes = make_nodes(runner)
    current: FlowState = {"phase": runner.ctx.load_state().phase.value, **(state or {})}
    step = "next_gate"
    while step is not None:
        current.update(nodes[step](current))
        if step == "next_gate":
            step = None if route_after_next_gate(current) == "end" else "evaluate"
        elif step == "evaluate":
            step = "stop" if route_after_evaluate(current) == "stop" else "advance"
        elif step == "advance":
            step = "next_gate"
        else:  # stop
            step = None
    return current


def invoke_flow(runner, state: FlowState | None = None) -> tuple[FlowState, str]:
    """跑完治理主线，返回 (最终状态, 引擎名 langgraph|interpreter)。"""
    compiled = build_langgraph(runner)
    if compiled is not None:
        initial = {"phase": runner.ctx.load_state().phase.value, **(state or {})}
        return compiled.invoke(initial, config={"recursion_limit": 50}), "langgraph"
    return walk_flow(runner, state), "interpreter"
