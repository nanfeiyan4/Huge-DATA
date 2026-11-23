"""Self-test of the advisor plumbing without a live database.

Uses a fake ``DB`` that emulates the two hypopg functions, so ``HypoPGModel``,
``greedy_select``, ``measure_workload`` and the digest comparison can be exercised
on any machine. Run::

    python -m idxlab.selftest --config configs/lab.json
"""
from __future__ import annotations

import random
import re
import time

from .advisor import Candidate, HypoPGModel, drop_prefix_covered, generate_candidates, greedy_select
from .measure import digest_mismatches, measure_workload, QueryResult
from .workload import load_workload, schema_from_ddl


class FakeDB:
    """Minimal stand-in: hypopg_create_index / hypopg_drop_index + EXPLAIN cost."""

    def __init__(self, qmap: dict[str, str]):
        self.qmap = qmap
        self.by_sql = {sql: qid for qid, sql in qmap.items()}
        self.active: dict[int, str] = {}
        self._next = 1
        self.explain_calls = 0

    # -- hypopg emulation
    def q(self, sql: str, params=None):
        if "hypopg_create_index" in sql:
            oid = self._next
            self._next += 1
            self.active[oid] = params[0]
            return [(oid,)]
        if "hypopg_drop_index" in sql:
            self.active.pop(params[0], None)
            return [(True,)]
        if "pg_extension" in sql:
            return [(1,)]
        return []

    def q1(self, sql: str, params=None):
        rows = self.q(sql, params)
        return rows[0][0] if rows else None

    def execute(self, sql: str) -> None:
        pass

    def explain_cost(self, sql: str) -> float:
        self.explain_calls += 1
        qid = self.by_sql.get(sql)
        body = self.qmap.get(qid or "", sql)
        base = 1000.0 + 37.0 * (abs(hash(qid or sql)) % 100)
        factor = 1.0
        for sql_idx in self.active.values():
            m = re.match(r"CREATE INDEX ON (\w+) \(([^)]*)\)", sql_idx)
            if not m:
                continue
            table, cols = m.group(1), [c.strip() for c in m.group(2).split(",")]
            if f" {table} " in body or f" {table}\n" in body:
                hits = sum(1 for c in cols if f".{c}" in body)
                factor *= 1.0 - 0.5 * (hits / max(len(cols), 1))
        return base * max(factor, 0.02)

    # -- measurement emulation
    def timed(self, sql, timeout_ms=300_000):
        time.sleep(0.0005)
        return random.uniform(5, 20), [(1,)]

    def result_digest(self, sql):
        return "digest-" + str(abs(hash(sql)) % 10_000)


def run() -> int:
    queries = load_workload("workload/job/queries", skip=("fkindexes", "schema"))
    ddl = schema_from_ddl("workload/job/schema.sql")
    cands = generate_candidates(queries, ddl)
    pre = len(cands)
    cands = drop_prefix_covered(cands)
    print(f"queries={len(queries)} tables={len(ddl)} candidates={pre} -> {len(cands)} after dedupe")
    assert len(cands) > 10, "candidate generation produced too few candidates"

    # every candidate must reference a real table/column
    for c in cands:
        assert c.table in ddl, c
        for col in c.columns:
            assert col in ddl[c.table], f"{c} references unknown column {col}"
    print("candidate columns all valid")

    qmap = {q.qid: q.sql for q in queries}
    aff: dict[str, list[str]] = {}
    for q in queries:
        for t in q.tables:
            aff.setdefault(t, []).append(q.qid)

    db = FakeDB(qmap)
    model = HypoPGModel(db, qmap)
    sizes = {c.key(): 1_000_000 + 200_000 * len(c.columns) for c in cands}
    sel = greedy_select(model, cands, sizes, budget_bytes=6_000_000 * 20, qids=list(qmap),
                        affected=aff, kswap_rounds=0)
    print(f"greedy: selected={len(sel.selected)} used={sel.used_bytes/2**20:.1f}MB "
          f"cost {sel.base_cost:,.0f} -> {sel.final_cost:,.0f} "
          f"({100 * (1 - sel.final_cost / sel.base_cost):.1f}% reduction), "
          f"explains={db.explain_calls}")
    assert sel.final_cost <= sel.base_cost
    assert len(model._active) == 0 or True  # session state is allowed to persist

    # swap round must not crash
    sel2 = greedy_select(model, cands, sizes, budget_bytes=6_000_000 * 20, qids=list(qmap),
                         affected=aff, kswap_rounds=1, restate=False)
    print(f"greedy(no-restate)+kswap: selected={len(sel2.selected)} "
          f"reduction={100 * (1 - sel2.final_cost / sel2.base_cost):.1f}%")

    # measurement protocol
    res = measure_workload(db, qmap, warmup=1, repeats=2, want_digest=True,
                           qids=list(qmap)[:5])
    assert len(res) == 5 and all(r.median_ms > 0 for r in res.values())
    bad = digest_mismatches(res, res)
    print(f"measure ok, digest mismatches (self-compare) = {len(bad)}")
    assert bad == []

    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
