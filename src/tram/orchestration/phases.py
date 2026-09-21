"""Phase transitions - pure functions, kept LangGraph-free until T1.10 wraps them."""

from __future__ import annotations

from tram.models.gates import GateResult, GateStatus
from tram.models.state import Phase, ProjectState

# Strict ordering: a gate only advances the phase from its expected predecessor.
PHASE_AFTER_GATE: dict[str, Phase] = {
    "g0_charter_gate": Phase.PLANNING,
    "g1_planning_gate": Phase.EXECUTING,
    "g2_quality_gate": Phase.CLOSING,
    "g3_closing_gate": Phase.DONE,
}

_EXPECTED_PHASE_BEFORE: dict[str, Phase] = {
    "g0_charter_gate": Phase.INITIATING,
    "g1_planning_gate": Phase.PLANNING,
    "g2_quality_gate": Phase.EXECUTING,
    "g3_closing_gate": Phase.CLOSING,
}


def apply_gate_result(state: ProjectState, result: GateResult) -> Phase | None:
    """Advance the phase on a passing gate; return the new phase or None."""
    if result.status != GateStatus.PASS:
        return None
    if _EXPECTED_PHASE_BEFORE.get(result.gate_id) != state.phase:
        return None  # out-of-order gate run: record but do not advance
    new_phase = PHASE_AFTER_GATE[result.gate_id]
    state.phase = new_phase
    return new_phase
