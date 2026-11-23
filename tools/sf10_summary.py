"""Summarise the SF10 run into out10/report_data10.json (+ budget curve)."""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path("/root/idx_lab/out10")
FIG = Path("/root/idx_lab/figures_sf10")
FIG.mkdir(exist_ok=True)


def js(name: str) -> dict | None:
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def compare(base: dict, tuned: dict) -> dict:
    bp = {q: v["median_ms"] for q, v in base["results"].items()}
    tp = {q: v["median_ms"] for q, v in tuned["results"].items()}
    qids = sorted(set(bp) & set(tp))
    sp = [bp[q] / tp[q] for q in qids if tp[q]]
    mism = [q for q in qids if base["results"][q].get("digest")
            and tuned["results"][q].get("digest")
            and base["results"][q]["digest"] != tuned["results"][q]["digest"]]
    return {
        "total_base_ms": round(sum(bp.values()), 1), "total_ms": round(sum(tp.values()), 1),
        "speedup": round(sum(bp.values()) / max(sum(tp.values()), 1e-9), 3),
        "geomean": round(statistics.geometric_mean(sp), 3) if sp else None,
        "mismatches": mism,
        "per_query": [{"q": q, "base_ms": round(bp[q], 1), "tuned_ms": round(tp[q], 1),
                       "speedup": round(bp[q] / max(tp[q], 1e-9), 2)} for q in qids],
    }


def main() -> int:
    base = js("base.json")
    if not base:
        print("no base.json yet")
        return 1
    data = {"scale": "SF10", "rows": []}
    for pct in (5, 20):
        rec, tun = js(f"greedy{pct}.json"), js(f"tuned{pct}.json")
        if not (rec and tun):
            continue
        c = compare(base, tun)
        data["rows"].append({
            "budget_pct": pct, "budget_mb": rec["budget_mb"], "used_mb": rec["used_mb"],
            "n_indexes": rec["n_selected"], "planner_red_pct": rec["cost_reduction_pct"],
            **{k: c[k] for k in ("speedup", "geomean", "mismatches")},
            "total_base_ms": c["total_base_ms"], "total_ms": c["total_ms"],
            "per_query": c["per_query"],
            "indexes": rec["indexes"],
        })
    (OUT / "report_data10.json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    if data["rows"]:
        r = data["rows"][0]
        print(f"{len(data['rows'])} configs; base {r['total_base_ms']/1000:.1f}s; "
              f"@5% {r['speedup']}x (geomean {r['geomean']})")

        fig, ax = plt.subplots(figsize=(5.6, 4))
        mb = [x["used_mb"] for x in data["rows"]]
        ax.plot(mb, [x["speedup"] for x in data["rows"]], "o-", label="total time speedup")
        ax.plot(mb, [x["geomean"] for x in data["rows"]], "s--", label="geometric mean")
        ax.axhline(1.0, color="#888", lw=0.8, ls=":")
        for x in data["rows"]:
            ax.annotate(f"{x['budget_pct']}%", (x["used_mb"], x["speedup"]),
                        textcoords="offset points", xytext=(0, -14), fontsize=8)
        ax.set_xlabel("index storage used (MB)")
        ax.set_ylabel("measured speedup")
        ax.set_title("Budget vs benefit (TPC-H SF10)")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(FIG / "budget_curve_sf10.png", dpi=160)
        plt.close(fig)

        for x in data["rows"]:
            pct = x["budget_pct"]
            rows = sorted(x["per_query"], key=lambda r: -r["speedup"])
            fig, ax = plt.subplots(figsize=(11, 4.2))
            colors = ["#c0392b" if r["speedup"] < 1 else "#27ae60" for r in rows]
            ax.bar([f"Q{r['q']}" for r in rows], [r["speedup"] for r in rows], color=colors)
            ax.axhline(1.0, color="#333", lw=0.8, ls="--")
            ax.set_ylabel("speedup (base / tuned)")
            ax.set_xlabel("TPC-H query")
            ax.set_title(f"Per-query speedup, SF10, budget {pct}%")
            ax.tick_params(axis="x", rotation=90, labelsize=8)
            fig.tight_layout()
            fig.savefig(FIG / f"speedup_per_query_sf10_{pct}.png", dpi=160)
            plt.close(fig)
    print("wrote", OUT / "report_data10.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
