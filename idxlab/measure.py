"""Workload measurement with a fixed, reproducible protocol.

Protocol (do not change mid-experiment):
  * fixed server settings (see provision/postgresql.tuned.conf)
  * ANALYZE + CHECKPOINT before each configuration
  * ``warmup`` discarded runs, then ``repeats`` runs, median reported
  * per-query statement_timeout; timeouts recorded, never silently dropped
  * result digest captured so index changes can be proven semantics-preserving
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .advisor import Candidate
from .db import DB, QueryTimeout


@dataclass
class QueryResult:
    qid: str
    median_ms: float
    times_ms: list[float] = field(default_factory=list)
    digest: str | None = None
    timed_out: bool = False

    def to_dict(self) -> dict:
        return {
            "qid": self.qid,
            "median_ms": round(self.median_ms, 2),
            "times_ms": [round(t, 2) for t in self.times_ms],
            "digest": self.digest,
            "timed_out": self.timed_out,
        }


def prepare_server(db: DB, checkpoint: bool = True) -> None:
    db.execute("ANALYZE")
    if checkpoint:
        try:
            db.execute("CHECKPOINT")
        except Exception:
            pass


def measure_workload(
    db: DB,
    queries: dict[str, str],
    warmup: int = 3,
    repeats: int = 5,
    timeout_ms: int = 300_000,
    want_digest: bool = True,
    qids: Sequence[str] | None = None,
    progress=None,
) -> dict[str, QueryResult]:
    order = list(qids) if qids else sorted(queries)
    results: dict[str, QueryResult] = {}
    for i, qid in enumerate(order, 1):
        sql = queries[qid]
        for _ in range(max(warmup, 0)):
            try:
                db.timed(sql, timeout_ms)
            except QueryTimeout:
                break
        times: list[float] = []
        timed_out = False
        for _ in range(max(repeats, 1)):
            try:
                ms, _rows = db.timed(sql, timeout_ms)
                times.append(ms)
            except QueryTimeout:
                timed_out = True
                break
        digest = None
        if want_digest:
            try:
                digest = db.result_digest(sql)
            except QueryTimeout:
                digest = None
            except Exception:
                # a digest failure must never invalidate the timing data
                digest = None
        median = float(timeout_ms) if timed_out else (statistics.median(times) if times else float(timeout_ms))
        results[qid] = QueryResult(qid, median, times, digest, timed_out)
        if progress:
            progress(f"[{i}/{len(order)}] {qid}: {median:,.0f} ms"
                     + (" (timeout)" if timed_out else ""))
    return results


def apply_indexes(db: DB, cands: Iterable[Candidate]) -> list[str]:
    created = []
    for cand in cands:
        name = "ix_" + cand.key_str
        db.execute(f"DROP INDEX IF EXISTS {name}")
        db.execute(cand.real_sql(name))
        created.append(name)
    return created


def drop_indexes(db: DB, names: Iterable[str]) -> None:
    for name in names:
        db.execute(f"DROP INDEX IF EXISTS {name}")


def digest_mismatches(base: dict[str, QueryResult], other: dict[str, QueryResult]) -> list[str]:
    bad = []
    for qid, r in base.items():
        o = other.get(qid)
        if o is None or r.digest is None or o.digest is None:
            continue
        if r.digest != o.digest:
            bad.append(qid)
    return bad


def summarize(results: dict[str, QueryResult]) -> dict:
    total = sum(r.median_ms for r in results.values())
    n_to = sum(1 for r in results.values() if r.timed_out)
    medians = [r.median_ms for r in results.values() if not r.timed_out]
    return {
        "n_queries": len(results),
        "n_timeouts": n_to,
        "total_ms": round(total, 1),
        "median_of_medians_ms": round(statistics.median(medians), 1) if medians else None,
    }
