#!/usr/bin/env bash
# TPC-H SF1 first experiment: baseline -> search -> tuned -> compare
set -u
export LC_ALL=C
cd /root/idx_lab && . .venv/bin/activate
LOG=/root/pipeline2.log
: > "$LOG"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "STEP 1/4 baseline"
python -m idxlab.cli --config configs/tpch.json measure --tag base >> "$LOG" 2>&1
log "baseline exit=$?"

log "STEP 2/4 search (hypopg, budget 20%, prescreen 25)"
python -m idxlab.cli --config configs/tpch.json search --tag greedy20 --prescreen 25 --kswap 1 >> "$LOG" 2>&1
log "search exit=$?"

log "STEP 3/4 tuned measure"
python -m idxlab.cli --config configs/tpch.json measure --tag tuned20 --indices out/greedy20.json >> "$LOG" 2>&1
log "tuned exit=$?"

log "STEP 4/4 compare"
python -m idxlab.cli --config configs/tpch.json compare --a out/base.json --b out/tuned20.json >> "$LOG" 2>&1
log "compare exit=$?"
log "ALL DONE"
