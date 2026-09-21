"""Gate runner: deterministic checks -> policy decision -> state + events."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from tram.cr_store import CRStore
from tram.governance.checks import CheckContext, run_check
from tram.models.events import EventKind
from tram.models.gates import GateResult, GateSpec, GateStatus
from tram.orchestration.phases import apply_gate_result

if TYPE_CHECKING:  # avoid a runtime import cycle: context -> policy_engine -> gate_runner
    from tram.context import TramContext

MAX_GATE_HISTORY = 50


class UnknownGateError(KeyError):
    pass


def load_gate_specs(gates_file: Path) -> dict[str, GateSpec]:
    raw = yaml.safe_load(gates_file.read_text(encoding="utf-8")) or {}
    return {
        gate_id: GateSpec.model_validate(body) for gate_id, body in raw.get("gates", {}).items()
    }


class GateRunner:
    def __init__(self, ctx: TramContext, engine=None) -> None:
        self.ctx = ctx
        self.engine = engine or ctx.make_policy_engine()

    def specs(self) -> dict[str, GateSpec]:
        gates_file = self.ctx.gates_file
        if not gates_file.exists():
            gates_file = self.ctx.packaged_policies / "gates.yaml"
        return load_gate_specs(gates_file)

    def run(self, gate_id: str) -> GateResult:
        specs = self.specs()
        if gate_id not in specs:
            raise UnknownGateError(f"unknown gate '{gate_id}'; known: {', '.join(sorted(specs))}")
        spec = specs[gate_id]

        check_ctx = CheckContext(
            repo=self.ctx.repo,
            artifacts_index=self.ctx.artifacts_index,
            git=self.ctx.git,
            events=self.ctx.events,
        )
        results = []
        for check_spec in spec.checks:
            result = run_check(check_spec, check_ctx)
            results.append(result)
            self.ctx.events.append(
                EventKind.CHECK_COMPLETED,
                source="gate_runner",
                data={
                    "gate_id": gate_id,
                    "check_id": result.id,
                    "status": result.status,
                    "output": result.output[:2000],
                },
                refs={"gate": gate_id},
            )

        state = self.ctx.state_store.load()
        open_crs = CRStore(self.ctx.crs_dir).open_crs()
        artifacts = check_ctx.load_artifacts()
        policy_input = {
            "gate_id": gate_id,
            "checks": [{"id": r.id, "status": r.status} for r in results],
            "open_crs": [cr.model_dump(mode="json") for cr in open_crs],
            "artifact_kinds": [a.kind for a in artifacts],
            "human_approvals": [a.model_dump(mode="json") for a in state.human_approvals],
        }
        decision = self.engine.decide(policy_input)

        round_num = state.remediation_rounds.get(gate_id, 0)
        if decision.allow:
            status = GateStatus.PASS
            state.remediation_rounds[gate_id] = 0
        else:
            round_num += 1
            state.remediation_rounds[gate_id] = round_num
            if decision.needs_human or round_num > spec.remediation_limit:
                status = GateStatus.BLOCKED_PENDING_HUMAN
            else:
                status = GateStatus.FAIL

        result = GateResult(
            gate_id=gate_id,
            ts=dt.datetime.now(dt.UTC),
            status=status,
            checks=results,
            decision_reasons=decision.reasons,
            needs_human=decision.needs_human or status == GateStatus.BLOCKED_PENDING_HUMAN,
            remediation_round=round_num,
        )
        state.gate_history.append(result)
        state.gate_history = state.gate_history[-MAX_GATE_HISTORY:]

        old_phase = state.phase
        new_phase = apply_gate_result(state, result)
        if new_phase:
            self.ctx.events.append(
                EventKind.PHASE_CHANGED,
                source="gate_runner",
                data={"from": old_phase.value, "to": new_phase.value},
                refs={"gate": gate_id},
            )
        self.ctx.state_store.save(state)
        self.ctx.events.append(
            EventKind.GATE_EVALUATED,
            source="gate_runner",
            data={
                "status": status.value,
                "reasons": decision.reasons,
                "remediation_round": round_num,
                "checks": {r.id: r.status for r in results},
            },
            refs={"gate": gate_id},
        )
        return result
