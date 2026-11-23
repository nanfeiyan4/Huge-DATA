#!/usr/bin/env bash
# Generate TPC-H data with duckdb (no dbgen compile) and load it into PostgreSQL.
# TPC-H is used here for: (a) an independent workload, (b) index maintenance cost.
#   Usage: DB=idxlab SF=10 bash provision/load_tpch.sh
set -euo pipefail

DB=${DB:-idxlab}
SF=${SF:-10}
OUT=${OUT:-$HOME/tpch_sf$SF}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PSQL=${PSQL:-psql -h 127.0.0.1 -p 5432 -U "${PGUSER:-$(whoami)}" -d "$DB"}
mkdir -p "$OUT" && cd "$OUT"

echo "==> generating TPC-H SF=$SF with duckdb"
python3 - <<PY
import duckdb
con = duckdb.connect("tpch.duckdb")
con.execute("INSTALL tpch")
con.execute("LOAD tpch")
con.execute("CALL dbgen(sf=${SF})")
for t in ["lineitem","orders","customer","part","supplier","partsupp","nation","region"]:
    con.execute(f"COPY {t} TO '${OUT}/{t}.csv' (HEADER, DELIMITER ',')")
    print("  dumped", t)
PY

echo "==> schema (TPC-H, primary keys only -> minimal baseline)"
$PSQL -v ON_ERROR_STOP=1 <<'SQL'
DROP TABLE IF EXISTS lineitem, orders, customer, part, supplier, partsupp, nation, region CASCADE;
CREATE TABLE region   (r_regionkey integer NOT NULL PRIMARY KEY, r_name char(25) NOT NULL, r_comment varchar(152));
CREATE TABLE nation   (n_nationkey integer NOT NULL PRIMARY KEY, n_name char(25) NOT NULL, n_regionkey integer NOT NULL, n_comment varchar(152));
CREATE TABLE part     (p_partkey integer NOT NULL PRIMARY KEY, p_name varchar(55) NOT NULL, p_mfgr char(25) NOT NULL,
                       p_brand char(10) NOT NULL, p_type varchar(25) NOT NULL, p_size integer NOT NULL,
                       p_container char(10) NOT NULL, p_retailprice numeric(15,2) NOT NULL, p_comment varchar(23) NOT NULL);
CREATE TABLE supplier (s_suppkey integer NOT NULL PRIMARY KEY, s_name char(25) NOT NULL, s_address varchar(40) NOT NULL,
                       s_nationkey integer NOT NULL, s_phone char(15) NOT NULL, s_acctbal numeric(15,2) NOT NULL,
                       s_comment varchar(101) NOT NULL);
CREATE TABLE partsupp (ps_partkey integer NOT NULL, ps_suppkey integer NOT NULL, ps_availqty integer NOT NULL,
                       ps_supplycost numeric(15,2) NOT NULL, ps_comment varchar(199) NOT NULL,
                       PRIMARY KEY (ps_partkey, ps_suppkey));
CREATE TABLE customer (c_custkey integer NOT NULL PRIMARY KEY, c_name varchar(25) NOT NULL, c_address varchar(40) NOT NULL,
                       c_nationkey integer NOT NULL, c_phone char(15) NOT NULL, c_acctbal numeric(15,2) NOT NULL,
                       c_mktsegment char(10) NOT NULL, c_comment varchar(117) NOT NULL);
CREATE TABLE orders   (o_orderkey bigint NOT NULL PRIMARY KEY, o_custkey integer NOT NULL, o_orderstatus char(1) NOT NULL,
                       o_totalprice numeric(15,2) NOT NULL, o_orderdate date NOT NULL, o_orderpriority char(15) NOT NULL,
                       o_clerk char(15) NOT NULL, o_shippriority integer NOT NULL, o_comment varchar(79) NOT NULL);
CREATE TABLE lineitem (l_orderkey bigint NOT NULL, l_partkey integer NOT NULL, l_suppkey integer NOT NULL,
                       l_linenumber integer NOT NULL, l_quantity numeric(15,2) NOT NULL, l_extendedprice numeric(15,2) NOT NULL,
                       l_discount numeric(15,2) NOT NULL, l_tax numeric(15,2) NOT NULL, l_returnflag char(1) NOT NULL,
                       l_linestatus char(1) NOT NULL, l_shipdate date NOT NULL, l_commitdate date NOT NULL,
                       l_receiptdate date NOT NULL, l_shipinstruct char(25) NOT NULL, l_shipmode char(10) NOT NULL,
                       l_comment varchar(44) NOT NULL, PRIMARY KEY (l_orderkey, l_linenumber));
SQL

echo "==> copying data"
for f in ./*.csv; do
  t=$(basename "$f" .csv)
  printf '  %-10s ' "$t"
  $PSQL -q -c "\copy ${t} FROM '${f}' CSV HEADER"
  echo ok
done

$PSQL -v ON_ERROR_STOP=1 -c "ANALYZE"
$PSQL -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY 1;"
$PSQL -c "SELECT pg_size_pretty(pg_database_size('${DB}')) AS db_size;"
echo "TPC-H load done. Queries: see https://github.com/electrum/tpch-dbgen (queries/)"
