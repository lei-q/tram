#!/usr/bin/env bash
# Tram vertical-slice smoke demo.
# Copies this demo project into a temp dir and drives the whole governance
# loop with the offline fake runner: init -> HITL -> blocked CR -> commit -> gate.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)/demo-project"
mkdir -p "$(dirname "$WORK")"
cp -R "$HERE" "$WORK"
cd "$WORK"
trap 'echo; echo "demo workdir kept at: $WORK"' EXIT

echo "==> git baseline"
git init -q -b main
git -c user.email=tram@demo -c user.name=tram add -A
git -c user.email=tram@demo -c user.name=tram commit -qm "demo baseline"

echo "==> tram init"
tram init --name demo

echo "==> human approves the scope baseline (HITL)"
tram baseline approve --by "$USER"

echo "==> agent task with an out-of-scope write (expect INTENT BLOCKED + CR)"
tram agent run --runner fake \
  --prompt "add mul" \
  --fake-plan plan-ok.json \
  --fake-violation-plan plan-bad.json

echo "==> fully in-scope agent task (expect committed to tram branch)"
tram agent run --runner fake --prompt "add mul" --fake-plan plan-ok.json

echo "==> intent guard on the host repo (expect ok)"
tram guard check

echo "==> quality gate while the scope CR is open (expect FAIL: open scope CR)"
tram gate run g2_quality_gate || true

echo "==> human rejects the out-of-scope CR (HITL)"
tram cr list
tram cr reject cr-0001 --by "$USER" --note "out of scope, do not merge"

echo "==> quality gate again (expect PASS: CR closed)"
tram gate run g2_quality_gate

echo "==> project status"
tram status

echo "==> replay the black box"
tram replay --limit 15
