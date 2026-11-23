#!/usr/bin/env bash
# TPC-H SF10 full experiment: generate -> load -> baseline -> recommend@5% ->
# measure -> recommend@20% -> measure -> compare. Machine must be idle.
set -u
export LC_ALL=C
export PGPASSWORD=idxlab
cd /root/idx_lab && . .venv/bin/activate
LOG=/root/sf10.log
: > "$LOG"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
CLI="python -m idxlab.cli --config configs/tpch_sf10.json"
PSQL="psql -h 127.0.0.1 -U idxlab"

log "STEP 0 generate SF10"
if [ ! -s /root/tpch_sf10/lineitem.csv ]; then
  tpchgen-cli csv -s 10 --output-dir /root/tpch_sf10 >> "$LOG" 2>&1
fi
du -sh /root/tpch_sf10 | tee -a "$LOG"

log "STEP 1 create database idxlab10"
$PSQL -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='idxlab10'" | grep -q 1 || \
  $PSQL -d postgres -q -c "CREATE DATABASE idxlab10" >> "$LOG" 2>&1

log "STEP 2 schema + baseline (PK + FK) indexes"
$PSQL -d idxlab10 -q -v ON_ERROR_STOP=1 -f workload/tpch/schema.sql >> "$LOG" 2>&1
$PSQL -d idxlab10 -q -v ON_ERROR_STOP=1 -f workload/tpch/fkindexes.sql >> "$LOG" 2>&1

log "STEP 3 load data"
for t in region nation part supplier partsupp customer orders lineitem; do
  n=$($PSQL -d idxlab10 -tAc "SELECT count(*) FROM $t")
  if [ "$n" = "0" ]; then
    log "  copy $t"
    /usr/bin/time -f "  %es" $PSQL -d idxlab10 -q -c "\copy $t FROM '/root/tpch_sf10/$t.csv' CSV HEADER" >> "$LOG" 2>&1
  fi
done
log "STEP 4 analyze"
$PSQL -d idxlab10 -q -c "ANALYZE" >> "$LOG" 2>&1
$PSQL -d idxlab10 -F'|' -A -t -c "SELECT 'db_size', pg_size_pretty(pg_database_size('idxlab10'))" | tee -a "$LOG"

log "STEP 5 baseline measurement"
$CLI measure --tag base >> "$LOG" 2>&1
log "baseline exit=$?"

for PCT in 5 20; do
  log "STEP 6 recommend @${PCT}%"
  $CLI pool --budget-pct "$PCT" >> "$LOG" 2>&1
  $CLI search --tag "greedy${PCT}" --prescreen 25 --kswap 1 >> "$LOG" 2>&1
  log "STEP 7 measure tuned @${PCT}%"
  $CLI measure --tag "tuned${PCT}" --indices "out10/greedy${PCT}.json" >> "$LOG" 2>&1
  log "STEP 8 compare @${PCT}%"
  $CLI compare --a out10/base.json --b "out10/tuned${PCT}.json" >> "$LOG" 2>&1
done

log "SF10 ALL DONE"
