"""Execution-rank candidates, then pack them under the storage budget.

Planner cost is a proxy; this script measures the *real* marginal benefit of every
candidate (build it once, time the affected queries, drop it), ranks by
measured-gain / storage, packs greedily under the budget and finally measures the
resulting index set with the full protocol.

    python tools/exec_rank.py --config configs/tpch.json \
        --sets out/greedy20.json out/norm20.json --tag exec20 \
        --budget-mb 202 --out out/exec20.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from idxlab.advisor import Candidate  # noqa: E402
from idxlab.cli import affected_map, load_config, load_queries, open_db  # noqa: E402
from idxlab.db import QueryTimeout  # noqa: E402


def median_run(db, sql: str, runs: int, timeout_ms: int) -> float:
    times = []
    for _ in range(runs):
        try:
            ms, _ = db.timed(sql, timeout_ms)
            times.append(ms)
        except QueryTimeout:
            times.append(float(timeout_ms))
    return statistics.median(times) if times else float(timeout_ms)


def measure(db, qmap: dict[str, str], runs: int, timeout_ms: int) -> dict[str, float]:
    return {qid: median_run(db, sql, runs, timeout_ms) for qid, sql in qmap.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--sets", nargs="+", required=True)
    ap.add_argument("--tag", default="exec")
    ap.add_argument("--budget-mb", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--timeout-ms", type=int, default=300_000)
    args = ap.parse_args()

    cfg = load_config(args.config)
    queries = load_queries(cfg)
    qmap = {q.qid: q.sql for q in queries}
    aff = affected_map(queries)

    cands, seen = [], set()
    for path in args.sets:
        for item in json.loads(Path(path).read_text(encoding="utf-8"))["indexes"]:
            cand = Candidate(item["table"], tuple(item["columns"]))
            if cand.key() not in seen:
                seen.add(cand.key())
                cands.append(cand)
    print(f"{len(cands)} unique candidates from {len(args.sets)} recommendation sets")

    db = open_db(cfg)
    built: list[str] = []
    try:
        for (name,) in db.q("SELECT indexname FROM pg_indexes WHERE schemaname='public' "
                            "AND (indexname LIKE 'ix_%' OR indexname LIKE 'ex_%')"):
            db.execute(f"DROP INDEX IF EXISTS {name}")

        base = measure(db, qmap, args.runs, args.timeout_ms)
        base_total = sum(base.values())
        print(f"baseline total {base_total/1000:.1f}s")

        gains = {}
        for cand in cands:
            name = "ex_" + cand.key_str
            t0 = time.perf_counter()
            db.execute(f"DROP INDEX IF EXISTS {name}")
            db.execute(cand.real_sql(name))
            build_s = time.perf_counter() - t0
            affected = [q for q in aff.get(cand.table, []) if q in qmap]
            after = measure(db, {q: qmap[q] for q in affected}, args.runs, args.timeout_ms)
            gain = sum(base[q] - after[q] for q in affected)
            size = int(db.q1("SELECT pg_relation_size(%s::regclass)", (name,)) or 0)
            db.execute(f"DROP INDEX IF EXISTS {name}")
            gains[cand.key()] = {"gain_ms": gain, "size": size, "build_s": build_s,
                                 "gain_per_mb": gain / max(size / 2**20, 1e-9)}
            print(f"  {cand.key_str:55s} gain {gain:8.0f} ms  size {size/2**20:7.1f} MB  "
                  f"build {build_s:5.1f}s")

        budget = int(args.budget_mb * 2**20)
        chosen, used = [], 0
        for key, info in sorted(gains.items(), key=lambda kv: -kv[1]["gain_per_mb"]):
            if info["gain_ms"] <= 0:
                continue
            if used + info["size"] > budget:
                continue
            chosen.append(key)
            used += info["size"]
        print(f"packed {len(chosen)} indexes, {used/2**20:.1f} MB of {args.budget_mb:.0f} MB budget")

        cand_by_key = {c.key(): c for c in cands}
        for key in chosen:
            cand = cand_by_key[key]
            name = "ex_" + cand.key_str
            db.execute(cand.real_sql(name))
            built.append(name)

        final = measure(db, qmap, args.runs, args.timeout_ms)
        final_total = sum(final.values())
        out = {
            "tag": args.tag,
            "candidates": [
                {"table": cand_by_key[k].table, "columns": list(cand_by_key[k].columns),
                 "measured_gain_ms": round(gains[k]["gain_ms"], 1),
                 "size_mb": round(gains[k]["size"] / 2**20, 2),
                 "build_s": round(gains[k]["build_s"], 1),
                 "selected": k in chosen}
                for k in gains
            ],
            "indices": [{"table": cand_by_key[k].table, "columns": list(cand_by_key[k].columns)}
                        for k in chosen],
            "measured": {"baseline_ms": round(base_total, 1), "final_ms": round(final_total, 1),
                         "speedup": round(base_total / max(final_total, 1e-9), 3),
                         "used_mb": round(used / 2**20, 2)},
            "per_query": {q: {"base_ms": round(base[q], 1), "final_ms": round(final[q], 1)}
                          for q in qmap},
        }
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"final {final_total/1000:.1f}s -> {base_total/max(final_total,1e-9):.2f}x  {args.out}")
        return 0
    finally:
        for name in built:
            try:
                db.execute(f"DROP INDEX IF EXISTS {name}")
            except Exception:
                pass
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
