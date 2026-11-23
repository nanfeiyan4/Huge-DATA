-- Baseline secondary indexes for TPC-H: the foreign-key indexes a real
-- deployment would already have (cf. JOB's fkindexes.sql).
-- The advisor's job is to choose *additional* indexes under a storage budget,
-- so the baseline must not be a pathological "primary keys only" schema.
CREATE INDEX fk_lineitem_partkey   ON lineitem (l_partkey);
CREATE INDEX fk_lineitem_suppkey   ON lineitem (l_suppkey);
CREATE INDEX fk_lineitem_orderkey  ON lineitem (l_orderkey);
CREATE INDEX fk_partsupp_suppkey   ON partsupp (ps_suppkey);
CREATE INDEX fk_orders_custkey     ON orders (o_custkey);
CREATE INDEX fk_customer_nationkey ON customer (c_nationkey);
CREATE INDEX fk_supplier_nationkey ON supplier (s_nationkey);
CREATE INDEX fk_nation_regionkey   ON nation (n_regionkey);
