#!/usr/bin/env bash
# Tram vertical-slice smoke demo.
# Copies this demo project into a temp dir and drives the whole governance
# loop with the offline fake runner: init -> HITL -> blocked CR -> commit -> gate.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)/demo-project"
mkdir -p "$(dirname "$WORK")"
cp -R "$HERE" "$WORK"
# 副本从零开始：剥离源目录里可能残留的运行状态（本机试验过的 .tram/.git 等）
rm -rf "$WORK/.tram" "$WORK/.git" "$WORK/.coverage" "$WORK/__pycache__"
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

echo "==> generate charter, pass G0 (initiating -> planning)"
tram artifact generate charter
tram gate run g0_charter_gate

echo "==> generate all artifacts, pass G1 (planning -> executing)"
tram artifact generate --all
tram gate run g1_planning_gate

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

echo "==> EVM: the blocked T-001 burns 8 points for nothing (expect SPI/CPI breach + risks)"
tram task points T-001 --est 8 --spent 8
tram evm snapshot
tram evm show

echo "==> QA loop: reproduce a defect on T-002 (expect rework task T-003)"
tram qa fail T-002 --note "mul(2,3) 返回 5" --by "$USER"

echo "==> dev fixes the rework task through the rails (role prompt: dev)"
tram agent run --task T-003 --role dev --runner fake --prompt "修复 mul" --fake-plan plan-ok.json

echo "==> QA verifies the fix (expect defect closed)"
tram qa pass T-003 --note "复现测试转绿" --by "$USER"

echo "==> defect recurs after a verified fix (expect escape rate 50%)"
tram qa fail T-002 --note "复发：修复未生效" --by "$USER"

echo "==> KPI dashboard (gate/defect MTTR + rework/escape rates)"
tram kpi
tram kpi | grep -q "escape rate" && echo "   (escape rate counted ✅)"

echo "==> regenerate the risk register (now carries the EVM risks)"
tram artifact generate risk_register

echo "==> project status"
tram status

echo "==> replay the black box"
tram replay --limit 15
