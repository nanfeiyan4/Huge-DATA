#!/usr/bin/env bash
# Stage 2: waits for the sweep to finish, then planner-cost correlation,
# figures, and the execution-feedback ranking.
set -u
export LC_ALL=C
export PGPASSWORD=idxlab
LOG=/root/stage2.log
: > "$LOG"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "waiting for exp3 to finish"
for _ in $(seq 1 180); do
  pgrep -f run_exp3.sh >/dev/null || break
  sleep 10
done
log "exp3 finished (or timed out waiting)"

cd /root/idx_lab && . .venv/bin/activate
CLI="python -m idxlab.cli --config configs/tpch.json"

log "drop leftover advisor indexes"
for n in $(psql -h 127.0.0.1 -U idxlab -d idxlab -tAc "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND (indexname LIKE 'ix_%' OR indexname LIKE 'ex_%')"); do
  psql -h 127.0.0.1 -U idxlab -d idxlab -qc "DROP INDEX IF EXISTS $n" >> "$LOG" 2>&1
done

log "planner costs for greedy@20"
python tools/planner_costs.py --config configs/tpch.json --indices out/greedy20.json \
  --out out/planner_greedy20.json >> "$LOG" 2>&1

log "figures for greedy@20"
python tools/report_plots.py --base out/base.json --tuned out/tuned20.json \
  --planner out/planner_greedy20.json --outdir figures --label "greedy@20%" >> "$LOG" 2>&1

log "planner costs for raw40"
python tools/planner_costs.py --config configs/tpch.json --indices out/raw40.json \
  --out out/planner_raw40.json >> "$LOG" 2>&1

log "figures for raw40"
python tools/report_plots.py --base out/base.json --tuned out/tuned_raw40.json \
  --planner out/planner_raw40.json --outdir figures_raw40 --label "greedy@40%" >> "$LOG" 2>&1

log "execution-feedback ranking (union of greedy@20, norm@20, raw@40)"
python tools/exec_rank.py --config configs/tpch.json \
  --sets out/greedy20.json out/norm20.json out/raw40.json \
  --tag exec20 --budget-mb 202 --out out/exec20.json >> "$LOG" 2>&1

log "STAGE2 DONE"
