"""Command line entry points for the index-advisor lab.

Typical flow on the lab machine::

    python -m idxlab.cli parse   --config configs/lab.json
    python -m idxlab.cli pool    --config configs/lab.json --budget-pct 5
    python -m idxlab.cli measure --config configs/lab.json --tag base
    python -m idxlab.cli search  --config configs/lab.json --budget-pct 5 --tag greedy
    python -m idxlab.cli measure --config configs/lab.json --tag tuned --indices out/greedy.json
    python -m idxlab.cli compare --config configs/lab.json --a out/base.json --b out/tuned.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import advisor, measure
from .advisor import (
    Candidate, MockModel, drop_prefix_covered, generate_candidates, greedy_select,
    index_sizes, load_stats, merge_candidates, standalone_gains,
)
from .db import DB, DBConfig
from .workload import load_workload, schema_from_ddl

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def load_config(path: str | Path) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    cfg.setdefault("paths", {})
    cfg["paths"].setdefault("out", str(ROOT / "out"))
    cfg["paths"].setdefault("workload", str(ROOT / "workload" / "job"))
    cfg["paths"].setdefault("ddl", str(ROOT / "workload" / "job" / "schema.sql"))
    cfg["measure"] = {"warmup": 3, "repeats": 5, "timeout_ms": 300_000, **cfg.get("measure", {})}
    Path(cfg["paths"]["out"]).mkdir(parents=True, exist_ok=True)
    return cfg


def open_db(cfg: dict) -> DB:
    return DB(DBConfig.from_dict(cfg.get("db", {})), vendor=cfg.get("vendor"))


def workload_dir(cfg: dict) -> Path:
    d = Path(cfg["paths"]["workload"])
    return d / "queries" if (d / "queries").is_dir() else d


def load_queries(cfg: dict):
    return load_workload(workload_dir(cfg), skip=("fkindexes", "schema"), schema=schema(cfg))


def schema(cfg: dict) -> dict[str, set[str]]:
    return schema_from_ddl(cfg["paths"]["ddl"])


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}")


def affected_map(queries) -> dict[str, list[str]]:
    aff: dict[str, list[str]] = {}
    for q in queries:
        for t in q.tables:
            aff.setdefault(t, []).append(q.qid)
    return aff


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_parse(cfg: dict, args) -> None:
    queries = load_queries(cfg)
    out = Path(cfg["paths"]["out"]) / "features.json"
    dump(out, {"n_queries": len(queries), "queries": [q.to_dict() for q in queries]})
    tables = sorted({t for q in queries for t in q.tables})
    print(f"{len(queries)} queries, {len(tables)} tables")
    empty = [q.qid for q in queries if not q.eq_preds and not q.range_preds]
    if empty:
        print(f"warning: no predicates parsed for {len(empty)} queries: {empty[:8]}")


def cmd_pool(cfg: dict, args) -> None:
    queries = load_queries(cfg)
    sch = schema(cfg)
    db = open_db(cfg)
    try:
        stats = load_stats(db)
    finally:
        db.close()
    cands = generate_candidates(queries, sch, stats)
    pre = len(cands)
    if args.dedupe:
        cands = drop_prefix_covered(cands)
    sizes = index_sizes(cands, stats)
    base_bytes = int(sum(st.rows * max(sum(st.width.values()), 1.0) for st in stats.values()))
    budget = int(base_bytes * args.budget_pct / 100.0)
    dump(Path(cfg["paths"]["out"]) / "pool.json", {
        "n_candidates": len(cands), "n_before_dedupe": pre,
        "base_bytes": base_bytes, "budget_bytes": budget,
        "candidates": [
            {"table": c.table, "columns": list(c.columns), "size_bytes": sizes[c.key()]}
            for c in cands
        ],
    })


def _load_pool(cfg: dict) -> tuple[list[Candidate], dict[tuple, int], int]:
    p = Path(cfg["paths"]["out"]) / "pool.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    cands = [Candidate(c["table"], tuple(c["columns"])) for c in data["candidates"]]
    sizes = {(c["table"], tuple(c["columns"])): c["size_bytes"] for c in data["candidates"]}
    return cands, sizes, data["budget_bytes"]


def cmd_search(cfg: dict, args) -> None:
    queries = load_queries(cfg)
    qmap = {q.qid: q.sql for q in queries}
    aff = affected_map(queries)
    cands, sizes, budget = _load_pool(cfg)
    if args.prescreen:
        db = open_db(cfg)
        try:
            model = advisor.HypoPGModel(db, qmap)
            gains = standalone_gains(model, cands, list(qmap), aff, objective=args.objective)
        finally:
            db.close()
        ranked = sorted(cands, key=lambda c: -(gains.get(c.key(), 0.0) / max(sizes.get(c.key(), 1), 1)))
        cands = ranked[: args.prescreen]
        print(f"prescreened to {len(cands)} candidates")

    if args.mock:
        model = MockModel({q.qid: q for q in queries})
    else:
        db = open_db(cfg)
        model = (advisor.HypoPGModel if not args.real_index else advisor.RealIndexModel)(db, qmap)
    try:
        sel = greedy_select(model, cands, sizes, budget, list(qmap), aff,
                            restate=not args.no_restate, kswap_rounds=args.kswap,
                            objective=args.objective,
                            log=print if args.verbose else None)
    finally:
        if not args.mock:
            db.close()
    out = Path(cfg["paths"]["out"]) / f"{args.tag}.json"
    dump(out, sel.summary() | {"steps": [s.__dict__ | {"candidate": s.candidate.key_str} for s in sel.steps]})
    print(json.dumps(sel.summary(), ensure_ascii=False, indent=2))


def cmd_measure(cfg: dict, args) -> None:
    queries = load_queries(cfg)
    qmap = {q.qid: q.sql for q in queries}
    db = open_db(cfg)
    created: list[str] = []
    try:
        drop = cfg.get("drop_advisor_indexes", True)
        if drop:
            for (name,) in db.q(
                "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND indexname LIKE 'ix_%'"
            ):
                db.execute(f"DROP INDEX IF EXISTS {name}")
        if args.indices:
            data = json.loads(Path(args.indices).read_text(encoding="utf-8"))
            cands = [Candidate(i["table"], tuple(i["columns"]), i.get("source", "rule"))
                     for i in data["indexes"]]
            created = measure.apply_indexes(db, cands)
            print(f"created {len(created)} indexes: {created}")
        measure.prepare_server(db)
        res = measure.measure_workload(
            db, qmap, warmup=cfg["measure"]["warmup"], repeats=cfg["measure"]["repeats"],
            timeout_ms=cfg["measure"]["timeout_ms"],
            progress=lambda m: print(m, flush=True),
        )
    finally:
        if args.drop_after:
            measure.drop_indexes(db, created)
        db.close()
    out = Path(cfg["paths"]["out"]) / f"{args.tag}.json"
    dump(out, {
        "tag": args.tag,
        "created_indexes": created,
        "summary": measure.summarize(res),
        "results": {qid: r.to_dict() for qid, r in res.items()},
    })


def cmd_compare(cfg: dict, args) -> None:
    a = json.loads(Path(args.a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))
    qids = sorted(set(a["results"]) & set(b["results"]))
    rows, mismatches = [], []
    for qid in qids:
        ra, rb = a["results"][qid], b["results"][qid]
        if ra["digest"] and rb["digest"] and ra["digest"] != rb["digest"]:
            mismatches.append(qid)
        speed = ra["median_ms"] / rb["median_ms"] if rb["median_ms"] else 0.0
        rows.append((qid, ra["median_ms"], rb["median_ms"], speed))
    rows.sort(key=lambda r: r[3])
    print(f"{'query':8} {'base_ms':>10} {'tuned_ms':>10} {'speedup':>8}")
    for qid, ta, tb, sp in rows:
        print(f"{qid:8} {ta:10,.0f} {tb:10,.0f} {sp:8.2f}x")
    base_total = sum(r[1] for r in rows)
    tuned_total = sum(r[2] for r in rows)
    import statistics
    geo = statistics.geometric_mean([r[3] for r in rows if r[3] > 0]) if rows else 0.0
    print(f"\ntotal: {base_total:,.0f} ms -> {tuned_total:,.0f} ms "
          f"({base_total / max(tuned_total, 1e-9):.2f}x), geomean {geo:.2f}x")
    print(f"correctness mismatches: {mismatches if mismatches else 'none'}")


def cmd_mocktest(cfg: dict, args) -> None:
    """End-to-end sanity check of parsing + generation + search, no database."""
    queries = load_queries(cfg)
    sch = schema(cfg)
    cands = generate_candidates(queries, sch)
    print(f"queries={len(queries)} candidates={len(cands)}")
    pre = len(cands)
    cands = drop_prefix_covered(cands)
    print(f"after prefix dedupe: {len(cands)} (removed {pre - len(cands)})")
    sizes = {c.key(): 1_000_000 + 250_000 * len(c.columns) for c in cands}
    aff = affected_map(queries)
    qmap = {q.qid: q for q in queries}
    model = MockModel(qmap)
    sel = greedy_select(model, cands, sizes, budget_bytes=8 * 2**20 * 1000,
                        qids=list(qmap), affected=aff, log=None)
    print(json.dumps(sel.summary(), ensure_ascii=False, indent=2)[:1200])


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="idxlab")
    ap.add_argument("--config", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("parse").set_defaults(func=cmd_parse)

    p = sub.add_parser("pool")
    p.add_argument("--budget-pct", type=float, default=5.0)
    p.add_argument("--dedupe", action="store_true", default=True)
    p.add_argument("--no-dedupe", dest="dedupe", action="store_false")
    p.set_defaults(func=cmd_pool)

    p = sub.add_parser("search")
    p.add_argument("--tag", default="greedy")
    p.add_argument("--prescreen", type=int, default=0)
    p.add_argument("--kswap", type=int, default=1)
    p.add_argument("--no-restate", action="store_true")
    p.add_argument("--objective", choices=["raw", "normalized"], default="raw")
    p.add_argument("--real-index", action="store_true")
    p.add_argument("--mock", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("measure")
    p.add_argument("--tag", required=True)
    p.add_argument("--indices", default=None)
    p.add_argument("--drop-after", action="store_true")
    p.set_defaults(func=cmd_measure)

    p = sub.add_parser("compare")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.set_defaults(func=cmd_compare)

    sub.add_parser("mocktest").set_defaults(func=cmd_mocktest)

    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    args.func(cfg, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
