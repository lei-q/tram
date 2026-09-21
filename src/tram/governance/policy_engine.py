"""Policy engines: OPA (primary, subprocess) + built-in Python fallback.

Both engines MUST stay semantically aligned with policies/gates.rego.
The parity test cross-checks them whenever the `opa` binary is available.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from tram.config import PolicyEngineKind, TramConfig

DECISION_QUERY = "data.tram.gates.decision"
CHANGE_QUERY = "data.tram.gates.human_approval_required"

G1_ARTIFACTS = {"wbs", "schedule", "quality_plan", "risk_register"}
HUMAN_APPROVAL_CR_TYPES = {"scope", "architecture", "release", "major_cost"}
GATE_IDS = {"g0_charter_gate", "g1_planning_gate", "g2_quality_gate", "g3_closing_gate"}


class PolicyEngineError(RuntimeError):
    pass


class PolicyDecision(BaseModel):
    allow: bool = False
    needs_human: bool = False
    reasons: list[str] = Field(default_factory=list)


class OpaPolicyEngine:
    """Evaluates gates.rego via the `opa` binary (rego v1, OPA >= 0.59)."""

    def __init__(self, rego_path: Path, binary: str = "opa") -> None:
        self.rego_path = rego_path
        self.binary = binary

    def _eval(self, query: str, data: dict):
        try:
            proc = subprocess.run(
                [
                    self.binary,
                    "eval",
                    "--format=json",
                    "--data",
                    str(self.rego_path),
                    "--stdin-input",
                    query,
                ],
                input=json.dumps(data),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise PolicyEngineError(f"opa binary not found: {self.binary}") from exc
        if proc.returncode != 0:
            raise PolicyEngineError(f"opa eval failed: {proc.stderr.strip()}")
        try:
            payload = json.loads(proc.stdout)
            return payload["result"][0]["expressions"][0]["value"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise PolicyEngineError(f"unexpected opa output: {proc.stdout[:500]}") from exc

    def decide(self, data: dict) -> PolicyDecision:
        return PolicyDecision.model_validate(self._eval(DECISION_QUERY, data))

    def change_requires_human(self, change: dict) -> bool:
        return bool(self._eval(CHANGE_QUERY, {"change": change}))


class FallbackPolicyEngine:
    """Pure-Python mirror of gates.rego for machines without the opa binary."""

    def decide(self, data: dict) -> PolicyDecision:
        gate_id = data.get("gate_id", "")
        failed = [
            c["id"] for c in data.get("checks", []) if c.get("status") not in ("pass", "skipped")
        ]
        open_scope = [
            r["id"]
            for r in data.get("open_crs", [])
            if r.get("type") == "scope" and r.get("status") not in ("rejected", "implemented")
        ]
        approvals = data.get("human_approvals", [])
        kinds = set(data.get("artifact_kinds", []))

        def approved(kind: str) -> bool:
            return any(a.get("kind") == kind and a.get("decision") == "approved" for a in approvals)

        reasons: list[str] = []
        needs_human = False
        allow = False

        if failed:
            reasons.append(f"failed checks: {failed}")
        if gate_id not in GATE_IDS:
            reasons.append(f"unknown gate: {gate_id}")
        elif gate_id == "g0_charter_gate":
            if not approved("scope_baseline"):
                needs_human = True
                reasons.append("scope baseline awaiting human approval")
            allow = not failed and approved("scope_baseline")
        elif gate_id == "g1_planning_gate":
            missing = sorted(a for a in G1_ARTIFACTS if a not in kinds)
            if missing:
                reasons.append(f"missing artifacts: {missing}")
            allow = not failed and not missing
        elif gate_id == "g2_quality_gate":
            if open_scope:
                reasons.append("open scope change request")
            allow = not failed and not open_scope
        elif gate_id == "g3_closing_gate":
            if not approved("release"):
                needs_human = True
                reasons.append("release awaiting human approval")
            allow = not failed and approved("release")

        return PolicyDecision(allow=allow, needs_human=needs_human, reasons=reasons)

    def change_requires_human(self, change: dict) -> bool:
        return change.get("type") in HUMAN_APPROVAL_CR_TYPES


def make_policy_engine(config: TramConfig, repo: Path, packaged_policies_dir: Path):
    override = repo / ".tram" / "policies" / "gates.rego"
    rego = override if override.exists() else packaged_policies_dir / "gates.rego"
    kind = config.policy_engine
    if kind == PolicyEngineKind.PYTHON:
        return FallbackPolicyEngine()
    if kind == PolicyEngineKind.OPA:
        if not shutil.which("opa"):
            raise PolicyEngineError(
                "policy_engine=opa but the opa binary was not found (brew install opa)"
            )
        return OpaPolicyEngine(rego)
    # auto: prefer OPA, fall back silently
    if shutil.which("opa"):
        return OpaPolicyEngine(rego)
    return FallbackPolicyEngine()
