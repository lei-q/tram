package tram.gates

import rego.v1

# Decision object consumed by the gate runner (OPA >= 0.59, Rego v1).
decision := {
	"allow": allow,
	"needs_human": needs_human,
	"reasons": reasons,
}

default allow := false

default needs_human := false

default human_approval_required := false

reasons contains msg if {
	count(failed_checks) > 0
	msg := sprintf("failed checks: %v", [failed_checks])
}

reasons contains "open scope change request" if {
	input.gate_id == "g2_quality_gate"
	count(open_scope_crs) > 0
}

reasons contains msg if {
	input.gate_id == "g1_planning_gate"
	count(g1_missing) > 0
	msg := sprintf("missing artifacts: %v", [g1_missing])
}

reasons contains "scope baseline awaiting human approval" if {
	input.gate_id == "g0_charter_gate"
	not human_approved("scope_baseline")
}

reasons contains "release awaiting human approval" if {
	input.gate_id == "g3_closing_gate"
	not human_approved("release")
}

failed_checks contains c.id if {
	some c in input.checks
	not check_passed(c)
}

check_passed(c) if c.status == "pass"

check_passed(c) if c.status == "skipped"

open_scope_crs contains r.id if {
	some r in input.open_crs
	r.type == "scope"
	r.status != "rejected"
	r.status != "implemented"
}

g1_artifacts := {"wbs", "schedule", "quality_plan", "risk_register"}

g1_missing contains kind if {
	some kind in g1_artifacts
	not artifact_present(kind)
}

artifact_present(kind) if {
	some k in input.artifact_kinds
	k == kind
}

allow if {
	input.gate_id == "g0_charter_gate"
	count(failed_checks) == 0
	human_approved("scope_baseline")
}

allow if {
	input.gate_id == "g1_planning_gate"
	count(failed_checks) == 0
	count(g1_missing) == 0
}

allow if {
	input.gate_id == "g2_quality_gate"
	count(failed_checks) == 0
	count(open_scope_crs) == 0
}

allow if {
	input.gate_id == "g3_closing_gate"
	count(failed_checks) == 0
	human_approved("release")
}

needs_human if {
	input.gate_id == "g0_charter_gate"
	not human_approved("scope_baseline")
}

needs_human if {
	input.gate_id == "g3_closing_gate"
	not human_approved("release")
}

# Change requests of these types can never be auto-approved by an agent.
human_approval_required if {
	input.change.type in {"scope", "architecture", "release", "major_cost"}
}

human_approved(kind) if {
	some a in input.human_approvals
	a.kind == kind
	a.decision == "approved"
}
