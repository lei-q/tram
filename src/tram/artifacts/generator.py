"""Artifact generator - deterministic renderers over real project data.

MVP: templates render from state/events/git only (no LLM). The Scribe agent
polishes prose in Phase 2; evidence links stay deterministic either way.
Every artifact lands with YAML frontmatter carrying its evidence links and
verified flags, and is registered in .tram/artifacts/index.json.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from tram.context import TramContext
from tram.evidence.verifier import VerifyContext, verify_evidence
from tram.governance.gate_runner import load_gate_specs
from tram.models.artifacts import Artifact
from tram.models.events import EventKind, TramEvent
from tram.models.evidence import Evidence, EvidenceKind
from tram.models.gates import GateSpec
from tram.models.task import TaskStatus

TEMPLATES_DIR = Path(__file__).parent / "templates"
G1_KINDS = ("wbs", "schedule", "quality_plan", "risk_register")
ARTIFACT_KINDS = ("charter", "scope_baseline") + G1_KINDS


class UnknownArtifactKind(KeyError):
    pass


class ArtifactGenerator:
    def __init__(self, ctx: TramContext) -> None:
        self.ctx = ctx
        self.env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )

    # -- public ------------------------------------------------------------

    def generate(self, kind: str) -> Artifact:
        if kind not in ARTIFACT_KINDS:
            raise UnknownArtifactKind(
                f"unknown artifact kind '{kind}'; known: {', '.join(ARTIFACT_KINDS)}"
            )
        data, evidence, gaps = self._collect(kind)
        vctx = VerifyContext(repo=self.ctx.repo, git=self.ctx.git, events=self.ctx.events)
        for ev in evidence:
            ev.verified = verify_evidence(ev, vctx)

        rel_path = f".tram/artifacts/{kind}.md"
        content = self._render(kind, data, evidence, gaps)
        target = self.ctx.repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        artifact = Artifact(
            id=kind,
            kind=kind,
            path=rel_path,
            evidence=evidence,
            generated_by="tram.generator",
        )
        self._register(artifact)
        sha = self.ctx.git.current_sha() or ""
        self.ctx.events.append(
            EventKind.ARTIFACT_GENERATED,
            source="tram.artifact",
            data={"kind": kind, "path": rel_path, "gaps": gaps},
            refs={"artifact": kind, **({"commit": sha} if sha else {})},
        )
        return artifact

    # -- data collection ----------------------------------------------------

    def _collect(self, kind: str) -> tuple[dict, list[Evidence], list[str]]:
        state = self.ctx.load_state()
        evidence: list[Evidence] = []
        gaps: list[str] = []
        sha = self.ctx.git.current_sha()
        if sha:
            evidence.append(Evidence(kind=EvidenceKind.COMMIT, ref=sha))
        data: dict = {
            "project_name": state.project_name,
            "phase": state.phase.value,
            "now": _now(),
        }

        init = self._first(EventKind.PROJECT_INITIALIZED)
        if init:
            evidence.append(Evidence(kind=EvidenceKind.EVENT, ref=str(init.seq)))
            data["init_seq"] = init.seq

        if kind == "charter":
            pass

        elif kind == "scope_baseline":
            baseline = self.ctx.load_baseline()
            data.update(
                version=baseline.version,
                allowed_paths=baseline.allowed_paths,
                forbidden_paths=baseline.forbidden_paths,
            )
            approval = self._last_approval("scope_baseline")
            if approval:
                evidence.append(Evidence(kind=EvidenceKind.EVENT, ref=str(approval.seq)))
                data.update(
                    approved_by=approval.data.get("by"), approved_note=approval.data.get("note", "")
                )
            else:
                gaps.append("scope-baseline: 尚未获得人类批准（tram baseline approve）")

        elif kind in ("wbs", "schedule"):
            tasks = [
                {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status.value,
                    "est_points": t.est_points,
                    "spent_points": t.spent_points,
                    "rework_count": t.rework_count,
                }
                for t in state.tasks
            ]
            data.update(
                tasks=tasks,
                total_est=sum(t["est_points"] for t in tasks),
                total_spent=sum(t["spent_points"] for t in tasks),
                done=sum(1 for t in tasks if t["status"] == TaskStatus.DONE.value),
            )
            if not tasks:
                gaps.append(f"{kind}: 尚无任务记录（tram agent run 后自动登记）")

        elif kind == "quality_plan":
            data["gates"] = [
                {"id": gate_id, "checks": self._check_rows(spec)}
                for gate_id, spec in self._gate_specs().items()
            ]

        elif kind == "risk_register":
            data["risks"] = [
                {
                    "id": r.id,
                    "description": r.description,
                    "probability": r.probability,
                    "impact": r.impact,
                    "strategy": r.strategy.value,
                    "trigger": r.trigger_event_seq,
                    "status": r.status.value,
                }
                for r in state.risks
            ]
            for event in self._derived_risk_events():
                seq = event.seq
                evidence.append(Evidence(kind=EvidenceKind.EVENT, ref=str(seq)))
                violations = ", ".join(event.data.get("violations", []))
                data["risks"].append(
                    {
                        "id": f"r-auto-{seq:04d}",
                        "description": f"范围外改动被拦截: {violations}",
                        "probability": 3,
                        "impact": 4,
                        "strategy": "mitigate",
                        "trigger": seq,
                        "status": "open",
                        "auto": True,
                    }
                )

        return data, evidence, gaps

    def _derived_risk_events(self) -> list[TramEvent]:
        events = list(self.ctx.events.read(kind=EventKind.INTENT_BLOCKED))
        return events[-20:]

    def _first(self, kind: EventKind) -> TramEvent | None:
        return next(iter(self.ctx.events.read(kind=kind)), None)

    def _last_approval(self, approval_kind: str) -> TramEvent | None:
        found = None
        for event in self.ctx.events.read(kind=EventKind.HUMAN_DECISION):
            if event.data.get("kind") == approval_kind and event.data.get("decision") == "approved":
                found = event
        return found

    def _gate_specs(self) -> dict[str, GateSpec]:
        gates_file = self.ctx.gates_file
        if not gates_file.exists():
            gates_file = self.ctx.packaged_policies / "gates.yaml"
        return load_gate_specs(gates_file)

    @staticmethod
    def _check_rows(spec: GateSpec) -> list[dict]:
        return [{"id": c.id, "type": c.type.value} for c in spec.checks]

    # -- rendering / registration -------------------------------------------

    def _render(self, kind: str, data: dict, evidence: list[Evidence], gaps: list[str]) -> str:
        body = self.env.get_template(f"{kind}.md.j2").render(**data)
        frontmatter = yaml.safe_dump(
            {
                "artifact": kind,
                "generated_by": "tram.generator",
                "generated_at": _now(),
                "evidence": [
                    {"kind": e.kind.value, "ref": e.ref, "verified": e.verified} for e in evidence
                ],
                **({"evidence_gap": gaps} if gaps else {}),
            },
            sort_keys=False,
            allow_unicode=True,
        )
        return f"---\n{frontmatter}---\n\n{body}"

    def _register(self, artifact: Artifact) -> None:
        index = self.ctx.artifacts_index
        index.parent.mkdir(parents=True, exist_ok=True)
        items: list[Artifact] = []
        if index.exists():
            items = [
                Artifact.model_validate(obj)
                for obj in json.loads(index.read_text(encoding="utf-8"))
            ]
        items = [a for a in items if a.kind != artifact.kind]
        items.append(artifact)
        index.write_text(
            json.dumps([a.model_dump(mode="json") for a in items], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
