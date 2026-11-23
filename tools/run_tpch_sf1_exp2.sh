#!/usr/bin/env bash
# Compare objectives (raw planner cost vs per-query normalized) at one budget,
# then sweep budgets with the normalized objective. Assumes SF1 data loaded and
# baseline (PK + FK indexes) in place.
set -u
export LC_ALL=C
cd /root/idx_lab && . .venv/bin/activate
LOG=/root/exp2.log
: > "$LOG"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "NORMALIZED objective @20%"
python -m idxlab.cli --config configs/tpch.json pool --budget-pct 20 >> "$LOG" 2>&1
python -m idxlab.cli --config configs/tpch.json search --tag norm20 --prescreen 25 --kswap 1 \
  --objective normalized >> "$LOG" 2>&1
python -m idxlab.cli --config configs/tpch.json measure --tag tuned_norm20 --indices out/norm20.json >> "$LOG" 2>&1
python -m idxlab.cli --config configs/tpch.json compare --a out/base.json --b out/tuned_norm20.json >> "$LOG" 2>&1

for B in 5 10 40; do
  log "NORMALIZED objective @${B}%"
  python -m idxlab.cli --config configs/tpch.json pool --budget-pct "$B" >> "$LOG" 2>&1
  python -m idxlab.cli --config configs/tpch.json search --tag norm${B} --prescreen 25 --kswap 1 \
    --objective normalized >> "$LOG" 2>&1
  python -m idxlab.cli --config configs/tpch.json measure --tag tuned_norm${B} --indices out/norm${B}.json >> "$LOG" 2>&1
  python -m idxlab.cli --config configs/tpch.json compare --a out/base.json --b out/tuned_norm${B}.json >> "$LOG" 2>&1
done

log "ABLATIONS @20%: no-restate, no-dedupe, random"
python -m idxlab.cli --config configs/tpch.json pool --budget-pct 20 >> "$LOG" 2>&1
python -m idxlab.cli --config configs/tpch.json search --tag abl_norestate --prescreen 25 --kswap 0 \
  --no-restate --objective normalized >> "$LOG" 2>&1
python -m idxlab.cli --config configs/tpch.json pool --budget-pct 20 --no-dedupe >> "$LOG" 2>&1
python -m idxlab.cli --config configs/tpch.json search --tag abl_nodedupe --prescreen 25 --kswap 1 \
  --objective normalized >> "$LOG" 2>&1
python -m idxlab.cli --config configs/tpch.json pool --budget-pct 20 >> "$LOG" 2>&1

log "ALL DONE"
