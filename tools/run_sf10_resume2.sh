#!/usr/bin/env bash
# Resume SF10 after the missing hypopg extension: search + measure + compare for
# budget 5% and 20%. Baseline (out10/base.json) is already measured.
set -u
export LC_ALL=C
export PGPASSWORD=idxlab
cd /root/idx_lab && . .venv/bin/activate
LOG=/root/sf10c.log
: > "$LOG"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
CLI="python -m idxlab.cli --config configs/tpch_sf10.json"
PSQL="psql -h 127.0.0.1 -U idxlab -d idxlab10"

log "STEP 1 create hypopg extension in idxlab10"
sudo -u postgres psql -d idxlab10 -q -c "CREATE EXTENSION IF NOT EXISTS hypopg" >> "$LOG" 2>&1
$PSQL -tAc "SELECT extname FROM pg_extension WHERE extname='hypopg'" | tee -a "$LOG"

log "baseline summary"
python - <<'PY' | tee -a "$LOG"
import json
d = json.load(open("/root/idx_lab/out10/base.json"))
r = d["results"]
tot = sum(v["median_ms"] for v in r.values())
to = sum(1 for v in r.values() if v["timed_out"])
print(f"baseline total {tot/1000:.1f}s over {len(r)} queries, timeouts={to}")
PY

for PCT in 5 20; do
  log "STEP 2 pool @${PCT}%"
  $CLI pool --budget-pct "$PCT" >> "$LOG" 2>&1
  log "STEP 3 search @${PCT}%"
  $CLI search --tag "greedy${PCT}" --prescreen 25 --kswap 1 >> "$LOG" 2>&1
  log "STEP 4 measure tuned @${PCT}%"
  $CLI measure --tag "tuned${PCT}" --indices "out10/greedy${PCT}.json" >> "$LOG" 2>&1
  log "STEP 5 compare @${PCT}%"
  $CLI compare --a out10/base.json --b "out10/tuned${PCT}.json" >> "$LOG" 2>&1
done

log "STEP 6 summary"
python tools/sf10_summary.py >> "$LOG" 2>&1
log "SF10 RESUME DONE"
