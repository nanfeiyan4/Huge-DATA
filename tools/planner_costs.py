"""Compute per-query planner costs with and without a recommended index set.

This is the data behind RQ3 (does the what-if cost proxy correlate with the
measured speedup?). Planning only - it never executes the queries, so it is
safe to run next to an idle server but should not run during measurements.

Usage:
    python tools/planner_costs.py --config configs/tpch.json --indices out/greedy20.json \
        --out out/planner_costs20.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from idxlab.advisor import Candidate, HypoPGModel  # noqa: E402
from idxlab.cli import load_queries, open_db, load_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--indices", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cfg = load_config(args.config)
    queries = load_queries(cfg)
    qmap = {q.qid: q.sql for q in queries}
    data = json.loads(Path(args.indices).read_text(encoding="utf-8"))
    cands = [Candidate(i["table"], tuple(i["columns"]), i.get("source", "rule"))
             for i in data["indexes"]]

    db = open_db(cfg)
    try:
        model = HypoPGModel(db, qmap)
        model.set_active([])
        base = model.costs(list(qmap))
        model.set_active(cands)
        tuned = model.costs(list(qmap))
    finally:
        db.close()

    out = {
        "indices": [{"table": c.table, "columns": list(c.columns)} for c in cands],
        "per_query": {
            qid: {"cost_base": round(base[qid], 2), "cost_tuned": round(tuned[qid], 2),
                  "cost_ratio": round(tuned[qid] / base[qid], 4) if base[qid] else None}
            for qid in qmap
        },
        "total": {"cost_base": round(sum(base.values()), 2),
                  "cost_tuned": round(sum(tuned.values()), 2)},
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    red = 100 * (1 - out["total"]["cost_tuned"] / out["total"]["cost_base"])
    print(f"planner cost {out['total']['cost_base']:,.0f} -> {out['total']['cost_tuned']:,.0f} "
          f"({red:.1f}% lower) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
