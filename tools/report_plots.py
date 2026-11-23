"""Turn measurement JSONs into the report's figures and a markdown table.

    python tools/report_plots.py --base out/base.json --tuned out/tuned20.json \
        --planner out/planner_costs20.json --outdir figures
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def spearman(x: list[float], y: list[float]) -> float:
    def rank(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, idx in enumerate(order):
            r[idx] = pos + 1.0
        return r

    rx, ry = rank(x), rank(y)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def js(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--tuned", required=True)
    ap.add_argument("--planner", default=None)
    ap.add_argument("--outdir", default="figures")
    ap.add_argument("--label", default="tuned")
    args = ap.parse_args()

    base, tuned = js(args.base), js(args.tuned)
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    qids = sorted(set(base["results"]) & set(tuned["results"]),
                  key=lambda q: int(q) if q.isdigit() else 0)
    rows = []
    for qid in qids:
        b = base["results"][qid]["median_ms"]
        t = tuned["results"][qid]["median_ms"]
        rows.append((qid, b, t, b / t if t else float("inf")))
    rows.sort(key=lambda r: r[3])

    # ---- figure 1: per-query speedup
    fig, ax = plt.subplots(figsize=(11, 4.2))
    colors = ["#c0392b" if r[3] < 1 else "#27ae60" for r in rows]
    ax.bar([r[0] for r in rows], [r[3] for r in rows], color=colors)
    ax.axhline(1.0, color="#333", lw=0.8, ls="--")
    ax.set_ylabel("speedup (base / tuned)")
    ax.set_xlabel("TPC-H query")
    ax.set_title(f"Per-query speedup with recommended indexes ({args.label})")
    ax.tick_params(axis="x", rotation=90, labelsize=8)
    fig.tight_layout()
    fig.savefig(out / "speedup_per_query.png", dpi=160)
    plt.close(fig)

    # ---- figure 2: totals
    tb = sum(r[1] for r in rows) / 1000.0
    tt = sum(r[2] for r in rows) / 1000.0
    fig, ax = plt.subplots(figsize=(4.2, 4))
    ax.bar(["baseline", args.label], [tb, tt], color=["#7f8c8d", "#27ae60"])
    for i, v in enumerate([tb, tt]):
        ax.text(i, v, f"{v:,.1f}s", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("total workload time (s)")
    ax.set_title(f"Total time: {tb / max(tt, 1e-9):.2f}x")
    fig.tight_layout()
    fig.savefig(out / "total_time.png", dpi=160)
    plt.close(fig)

    # ---- figure 3: planner cost ratio vs measured speedup (RQ3)
    corr = None
    if args.planner and Path(args.planner).exists():
        pl = js(args.planner)["per_query"]
        xs, ys, labels = [], [], []
        for qid in qids:
            if qid not in pl:
                continue
            ratio = pl[qid]["cost_ratio"]
            if not ratio:
                continue
            xs.append(ratio)
            ys.append(dict((r[0], r[3]) for r in rows)[qid])
            labels.append(qid)
        if len(xs) >= 3:
            corr = spearman([-v for v in xs], ys)  # lower cost ratio => higher expected speedup
            fig, ax = plt.subplots(figsize=(5.2, 4.4))
            ax.scatter(xs, ys, s=28, color="#2980b9")
            for x, y, lb in zip(xs, ys, labels):
                ax.annotate(lb, (x, y), fontsize=7, xytext=(3, 3), textcoords="offset points")
            ax.axhline(1.0, color="#888", lw=0.7, ls="--")
            ax.set_xlabel("planner cost ratio (tuned / base)")
            ax.set_ylabel("measured speedup")
            ax.set_title(f"Cost proxy vs measured gain (Spearman {corr:.2f})")
            fig.tight_layout()
            fig.savefig(out / "planner_vs_measured.png", dpi=160)
            plt.close(fig)

    # ---- markdown table
    geom = statistics.geometric_mean([r[3] for r in rows if r[3] > 0])
    lines = [
        f"| query | baseline (ms) | {args.label} (ms) | speedup |",
        "|---|---|---|---|",
    ]
    for qid, b, t, sp in rows:
        lines.append(f"| {qid} | {b:,.0f} | {t:,.0f} | {sp:.2f}x |")
    lines.append(f"| **total** | **{sum(r[1] for r in rows):,.0f}** | "
                 f"**{sum(r[2] for r in rows):,.0f}** | "
                 f"**{sum(r[1] for r in rows) / sum(r[2] for r in rows):.2f}x** |")
    (out / "table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"n={len(rows)} total {tb:.1f}s -> {tt:.1f}s "
          f"({tb / max(tt, 1e-9):.2f}x), geomean {geom:.2f}x")
    if corr is not None:
        print(f"spearman(cost ratio, speedup) = {corr:.3f}")
    print(f"figures -> {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
