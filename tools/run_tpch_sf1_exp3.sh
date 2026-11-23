#!/usr/bin/env bash
# Budget sweep + ablations with the raw planner-cost objective (the objective that
# won the comparison), every configuration verified by real measurement.
set -u
export LC_ALL=C
cd /root/idx_lab && . .venv/bin/activate
LOG=/root/exp3.log
: > "$LOG"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
CLI="python -m idxlab.cli --config configs/tpch.json"

measure_cfg() {  # $1 = tag
  $CLI measure --tag "tuned_$1" --indices "out/$1.json" >> "$LOG" 2>&1
  $CLI compare --a out/base.json --b "out/tuned_$1.json" >> "$LOG" 2>&1
}

log "CONFIG raw @5%"
$CLI pool --budget-pct 5 >> "$LOG" 2>&1
$CLI search --tag raw5 --prescreen 25 --kswap 1 >> "$LOG" 2>&1
measure_cfg raw5

log "CONFIG raw @10%"
$CLI pool --budget-pct 10 >> "$LOG" 2>&1
$CLI search --tag raw10 --prescreen 25 --kswap 1 >> "$LOG" 2>&1
measure_cfg raw10

log "CONFIG raw @40%"
$CLI pool --budget-pct 40 >> "$LOG" 2>&1
$CLI search --tag raw40 --prescreen 25 --kswap 1 >> "$LOG" 2>&1
measure_cfg raw40

log "ABLATION no incremental re-estimation @20%"
$CLI pool --budget-pct 20 >> "$LOG" 2>&1
$CLI search --tag abl_norestate --prescreen 25 --kswap 0 --no-restate >> "$LOG" 2>&1
measure_cfg abl_norestate

log "ABLATION no prefix-dedupe @20%"
$CLI pool --budget-pct 20 --no-dedupe >> "$LOG" 2>&1
$CLI search --tag abl_nodedupe --prescreen 25 --kswap 1 >> "$LOG" 2>&1
measure_cfg abl_nodedupe

log "ALL DONE"
