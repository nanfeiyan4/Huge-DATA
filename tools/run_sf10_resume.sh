#!/usr/bin/env bash
# Reload lineitem without secondary indexes (much faster COPY), rebuild the FK
# indexes afterwards, then run the measurement pipeline.
set -u
export LC_ALL=C
export PGPASSWORD=idxlab
cd /root/idx_lab && . .venv/bin/activate
LOG=/root/sf10b.log
: > "$LOG"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
PSQL="psql -h 127.0.0.1 -U idxlab -d idxlab10"
CLI="python -m idxlab.cli --config configs/tpch_sf10.json"

log "STEP A truncate lineitem and drop its secondary indexes"
$PSQL -v ON_ERROR_STOP=1 -q -c "TRUNCATE lineitem" >> "$LOG" 2>&1
$PSQL -q -c "DROP INDEX IF EXISTS fk_lineitem_partkey" >> "$LOG" 2>&1
$PSQL -q -c "DROP INDEX IF EXISTS fk_lineitem_suppkey" >> "$LOG" 2>&1
$PSQL -q -c "DROP INDEX IF EXISTS fk_lineitem_orderkey" >> "$LOG" 2>&1
$PSQL -tAc "SELECT count(*) FROM pg_indexes WHERE tablename='lineitem'" | tee -a "$LOG"

log "STEP B reload lineitem (PK only)"
/usr/bin/time -f "  copy %es" $PSQL -q -c "\copy lineitem FROM '/root/tpch_sf10/lineitem.csv' CSV HEADER" >> "$LOG" 2>&1

log "STEP C rebuild FK indexes on lineitem"
/usr/bin/time -f "  fk partkey %es" $PSQL -q -c "CREATE INDEX fk_lineitem_partkey ON lineitem (l_partkey)" >> "$LOG" 2>&1
/usr/bin/time -f "  fk suppkey %es" $PSQL -q -c "CREATE INDEX fk_lineitem_suppkey ON lineitem (l_suppkey)" >> "$LOG" 2>&1
/usr/bin/time -f "  fk orderkey %es" $PSQL -q -c "CREATE INDEX fk_lineitem_orderkey ON lineitem (l_orderkey)" >> "$LOG" 2>&1

log "STEP D analyze"
/usr/bin/time -f "  analyze %es" $PSQL -q -c "ANALYZE" >> "$LOG" 2>&1
$PSQL -F'|' -A -t -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC" | tee -a "$LOG"
$PSQL -F'|' -A -t -c "SELECT 'db_size', pg_size_pretty(pg_database_size('idxlab10'))" | tee -a "$LOG"

log "STEP E baseline measurement"
$CLI measure --tag base >> "$LOG" 2>&1
log "baseline exit=$?"

for PCT in 5 20; do
  log "STEP F recommend @${PCT}%"
  $CLI pool --budget-pct "$PCT" >> "$LOG" 2>&1
  $CLI search --tag "greedy${PCT}" --prescreen 25 --kswap 1 >> "$LOG" 2>&1
  log "STEP G measure tuned @${PCT}%"
  $CLI measure --tag "tuned${PCT}" --indices "out10/greedy${PCT}.json" >> "$LOG" 2>&1
  log "STEP H compare @${PCT}%"
  $CLI compare --a out10/base.json --b "out10/tuned${PCT}.json" >> "$LOG" 2>&1
done

log "SF10 ALL DONE"
