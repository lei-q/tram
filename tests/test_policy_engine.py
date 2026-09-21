import shutil
from pathlib import Path

import pytest

from tram.context import PACKAGED_POLICIES
from tram.governance.policy_engine import (
    FallbackPolicyEngine,
    OpaPolicyEngine,
    PolicyEngineError,
)

G2 = "g2_quality_gate"
G0 = "g0_charter_gate"
G1 = "g1_planning_gate"
G3 = "g3_closing_gate"


def _input(gate: str, checks=None, crs=None, kinds=None, approvals=None) -> dict:
    return {
        "gate_id": gate,
        "checks": checks if checks is not None else [{"id": "c1", "status": "pass"}],
        "open_crs": crs or [],
        "artifact_kinds": kinds or [],
        "human_approvals": approvals or [],
    }


def _ok_check():
    return [{"id": "c1", "status": "pass"}]


def test_g2_passes_with_green_checks():
    assert FallbackPolicyEngine().decide(_input(G2)).allow is True


def test_g2_fails_on_failed_check():
    decision = FallbackPolicyEngine().decide(_input(G2, checks=[{"id": "c1", "status": "fail"}]))
    assert decision.allow is False
    assert any("failed checks" in r for r in decision.reasons)


def test_g2_fails_on_open_scope_cr():
    crs = [{"id": "cr-0001", "type": "scope", "status": "draft"}]
    decision = FallbackPolicyEngine().decide(_input(G2, crs=crs))
    assert decision.allow is False
    assert "open scope change request" in decision.reasons


def test_g2_ignores_closed_crs():
    crs = [
        {"id": "cr-0001", "type": "scope", "status": "rejected"},
        {"id": "cr-0002", "type": "scope", "status": "implemented"},
        {"id": "cr-0003", "type": "cost", "status": "draft"},
    ]
    assert FallbackPolicyEngine().decide(_input(G2, crs=crs)).allow is True


def test_g0_needs_human_without_approval():
    decision = FallbackPolicyEngine().decide(_input(G0))
    assert decision.allow is False
    assert decision.needs_human is True


def test_g0_passes_with_approval():
    approvals = [{"kind": "scope_baseline", "decision": "approved"}]
    assert FallbackPolicyEngine().decide(_input(G0, approvals=approvals)).allow is True


def test_g1_requires_all_artifacts():
    engine = FallbackPolicyEngine()
    kinds = ["wbs", "schedule", "quality_plan"]
    assert engine.decide(_input(G1, kinds=kinds)).allow is False
    kinds += ["risk_register"]
    assert engine.decide(_input(G1, kinds=kinds)).allow is True


def test_g3_needs_human_release_approval():
    engine = FallbackPolicyEngine()
    assert engine.decide(_input(G3)).needs_human is True
    approvals = [{"kind": "release", "decision": "approved"}]
    assert engine.decide(_input(G3, approvals=approvals)).allow is True


def test_unknown_gate_denied():
    decision = FallbackPolicyEngine().decide(_input("g9"))
    assert decision.allow is False
    assert any("unknown gate" in r for r in decision.reasons)


def test_change_requires_human():
    engine = FallbackPolicyEngine()
    assert engine.change_requires_human({"type": "scope"}) is True
    assert engine.change_requires_human({"type": "architecture"}) is True
    assert engine.change_requires_human({"type": "schedule"}) is False


@pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not installed")
def test_opa_and_fallback_agree():
    opa = OpaPolicyEngine(PACKAGED_POLICIES / "gates.rego")
    fallback = FallbackPolicyEngine()
    crs = [{"id": "cr-0001", "type": "scope", "status": "draft"}]
    approvals = [{"kind": "scope_baseline", "decision": "approved"}]
    cases = [
        _input(G2),
        _input(G2, checks=[{"id": "c1", "status": "fail"}]),
        _input(G2, crs=crs),
        _input(G0),
        _input(G0, approvals=approvals),
        _input(G1, kinds=["wbs", "schedule", "quality_plan", "risk_register"]),
        _input(G1, kinds=["wbs"]),
        _input(G3),
        _input("g9"),
    ]
    for case in cases:
        o = opa.decide(case)
        f = fallback.decide(case)
        assert (o.allow, o.needs_human) == (f.allow, f.needs_human), case["gate_id"]
    assert opa.change_requires_human({"type": "scope"}) is True
    assert opa.change_requires_human({"type": "schedule"}) is False


def test_opa_missing_binary_raises_cleanly(tmp_path: Path):
    engine = OpaPolicyEngine(PACKAGED_POLICIES / "gates.rego", binary="definitely-not-opa")
    with pytest.raises(PolicyEngineError):
        engine.decide(_input(G2))
