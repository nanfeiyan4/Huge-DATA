#!/usr/bin/env bash
# Load the JOB (Join-Order-Benchmark / IMDB) dataset into PostgreSQL.
#   schema.sql has PRIMARY KEYs only  -> that is the "no extra index" baseline.
#   Data: http://homepages.cwi.nl/~boncz/job/imdb.tgz  (~1.5 GB compressed, ~3.5 GB CSV)
#
# Usage: DB=idxlab DATA_DIR=$HOME/imdb bash provision/load_job.sh
set -euo pipefail

DB=${DB:-idxlab}
DATA_DIR=${DATA_DIR:-$HOME/imdb}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PSQL=${PSQL:-psql -h 127.0.0.1 -p 5432 -U "${PGUSER:-$(whoami)}" -d "$DB"}
mkdir -p "$DATA_DIR"

cd "$DATA_DIR"
if ! ls ./*.csv ./*.csv.gz >/dev/null 2>&1; then
  echo "==> downloading imdb.tgz"
  for url in "http://homepages.cwi.nl/~boncz/job/imdb.tgz" \
             "https://event.cwi.nl/da/job/imdb.tgz"; do
    if wget -q --show-progress -O imdb.tgz "$url"; then
      break
    fi
    echo "    failed: $url"
  done
  [ -s imdb.tgz ] || { echo "ERROR: could not download imdb.tgz"; exit 1; }
  echo "==> extracting"
  tar -xzf imdb.tgz
  # some mirrors ship csv.gz
  for f in ./*.csv.gz; do [ -e "$f" ] && gunzip -f "$f"; done
fi
ls -1 ./*.csv | head -5
du -sh .

echo "==> creating schema"
$PSQL -v ON_ERROR_STOP=1 -f "$HERE/../workload/job/schema.sql"

echo "==> copying tables"
load_one() {
  local table=$1 file=$2
  # CSV (no header) is the canonical format for imdb.tgz; fall back to header/text.
  if $PSQL -q -c "\copy ${table} FROM '${file}' CSV" 2>/dev/null; then
    return 0
  elif $PSQL -q -c "\copy ${table} FROM '${file}' CSV HEADER" 2>/dev/null; then
    return 0
  else
    $PSQL -q -c "\copy ${table} FROM '${file}'"
  fi
}

for f in ./*.csv; do
  table=$(basename "$f" .csv)
  printf '  %-20s ' "$table"
  load_one "$table" "$f"
  echo ok
done

echo "==> ANALYZE (default_statistics_target=500, takes a few minutes)"
$PSQL -v ON_ERROR_STOP=1 -c "ANALYZE"

echo "==> index inventory (baseline should be primary keys only)"
$PSQL -c "SELECT tablename, indexname FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2;"
$PSQL -c "SELECT pg_size_pretty(pg_database_size('${DB}')) AS db_size;"
