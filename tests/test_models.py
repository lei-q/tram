from datetime import date

from tram.models.artifacts import Artifact
from tram.models.cr import ChangeRequest, CRStatus, CRType
from tram.models.evidence import Evidence, EvidenceKind
from tram.models.evm import EVMSnapshot
from tram.models.gates import GateStatus
from tram.models.state import Phase, ProjectState


def test_evm_derived_fields():
    snap = EVMSnapshot(date=date(2026, 9, 21), pv=10, ev=8, ac=9)
    assert snap.spi == 0.8
    assert snap.cpi == 0.889
    assert snap.sv == -2.0
    assert snap.cv == -1.0


def test_evm_zero_denominators_are_safe():
    snap = EVMSnapshot(date=date(2026, 9, 21), pv=0, ev=0, ac=0)
    assert snap.spi == 0.0
    assert snap.cpi == 0.0


def test_artifact_verified_property():
    good = Artifact(
        id="a1",
        kind="wbs",
        path=".tram/artifacts/wbs.md",
        evidence=[Evidence(kind=EvidenceKind.COMMIT, ref="abc", verified=True)],
    )
    no_ev = Artifact(id="a2", kind="wbs", path="x.md")
    unverified = Artifact(
        id="a3",
        kind="wbs",
        path="y.md",
        evidence=[Evidence(kind=EvidenceKind.COMMIT, ref="abc", verified=False)],
    )
    assert good.verified and not no_ev.verified and not unverified.verified


def test_cr_lifecycle_statuses_exist():
    cr = ChangeRequest(id="cr-0001", type=CRType.SCOPE)
    assert cr.status == CRStatus.DRAFT
    assert cr.type == CRType.SCOPE


def test_state_helpers():
    state = ProjectState(project_name="demo")
    assert state.phase == Phase.INITIATING
    cr = ChangeRequest(id="cr-0001", type=CRType.SCOPE)
    state.open_crs.append("cr-0001")
    assert state.is_cr_open("cr-0001")
    assert state.open_change_requests([cr]) == [cr]


def test_gate_status_enum_roundtrip():
    assert GateStatus("pass") is GateStatus.PASS
