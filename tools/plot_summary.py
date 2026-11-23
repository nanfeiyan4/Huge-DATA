"""Summarise every run into report_data.json and draw the summary figures."""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path("/root/idx_lab/out")
FIG = Path("/root/idx_lab/figures")
FIG.mkdir(exist_ok=True)


def js(name: str) -> dict | None:
    p = OUT / name
    if not p.exists():
        print("missing", name)
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def totals(res: dict) -> tuple[float, dict[str, float]]:
    per = {q: v["median_ms"] for q, v in res["results"].items()}
    return sum(per.values()), per


def compare(base: dict, tuned: dict) -> dict:
    bt, bp = totals(base)
    tt, tp = totals(tuned)
    qids = sorted(set(bp) & set(tp))
    sp = [bp[q] / tp[q] for q in qids if tp[q]]
    mism = [q for q in qids if base["results"][q].get("digest")
            and tuned["results"][q].get("digest")
            and base["results"][q]["digest"] != tuned["results"][q]["digest"]]
    return {
        "total_base_ms": round(bt, 1), "total_ms": round(tt, 1),
        "speedup": round(bt / max(tt, 1e-9), 3),
        "geomean": round(statistics.geometric_mean(sp), 3) if sp else None,
        "mismatches": mism,
        "per_query": [{"q": q, "base_ms": round(bp[q], 1), "tuned_ms": round(tp[q], 1),
                       "speedup": round(bp[q] / max(tp[q], 1e-9), 2)} for q in qids],
    }


def main() -> int:
    data: dict = {}
    base = js("base.json")
    if not base:
        return 1

    # ---------------- RQ1: main configuration
    tuned20 = js("tuned20.json")
    rec20 = js("greedy20.json")
    if tuned20 and rec20:
        data["rq1"] = compare(base, tuned20) | {
            "n_indexes": rec20["n_selected"], "used_mb": rec20["used_mb"],
            "budget_mb": rec20["budget_mb"], "planner_red_pct": rec20["cost_reduction_pct"],
            "evals": rec20["evals"], "indexes": rec20["indexes"],
        }

    # ---------------- RQ2: budget sweep (raw objective)
    rows = []
    for pct, rec_name, tuned_name in [(5, "raw5.json", "tuned_raw5.json"),
                                      (10, "raw10.json", "tuned_raw10.json"),
                                      (20, "greedy20.json", "tuned20.json"),
                                      (40, "raw40.json", "tuned_raw40.json")]:
        rec, tun = js(rec_name), js(tuned_name)
        if not (rec and tun):
            continue
        c = compare(base, tun)
        rows.append({"budget_pct": pct, "budget_mb": rec["budget_mb"],
                     "used_mb": rec["used_mb"], "n_indexes": rec["n_selected"],
                     "planner_red_pct": rec["cost_reduction_pct"],
                     "speedup": c["speedup"], "geomean": c["geomean"]})
    data["rq2"] = {"rows": rows}

    # ---------------- RQ3: planner proxy quality
    prof: dict = {}
    for tag, plan_file, tuned_file in [("20", "planner_greedy20.json", "tuned20.json"),
                                       ("40", "planner_raw40.json", "tuned_raw40.json")]:
        pl, tun = js(plan_file), js(tuned_file)
        if not (pl and tun):
            continue
        c = compare(base, tun)
        sp = {r["q"]: r["speedup"] for r in c["per_query"]}
        common = [q for q in sp if q in pl["per_query"]]
        xs = [-pl["per_query"][q]["cost_ratio"] for q in common]
        ys = [sp[q] for q in common]

        def rank(v):
            order = sorted(range(len(v)), key=lambda i: v[i])
            r = [0.0] * len(v)
            for pos, i in enumerate(order):
                r[i] = pos + 1.0
            return r

        rx, ry = rank(xs), rank(ys)
        mx, my = statistics.fmean(rx), statistics.fmean(ry)
        num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
        den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
        prof[tag] = {
            "spearman": round(num / den, 3) if den else None,
            "planner_red_pct": round(100 * (1 - pl["total"]["cost_tuned"] / pl["total"]["cost_base"]), 2),
            "measured_speedup": c["speedup"],
            "points": [{"q": q, "cost_ratio": pl["per_query"][q]["cost_ratio"],
                        "speedup": sp[q]} for q in common],
        }
    data["rq3"] = prof

    # ---------------- RQ4: ablations
    abl = []
    for label, tuned_file, rec_file in [
        ("完整方法（增量重估 + 冗余消除）", "tuned20.json", "greedy20.json"),
        ("去掉增量重估", "tuned_abl_norestate.json", "abl_norestate.json"),
        ("去掉前缀冗余消除", "tuned_abl_nodedupe.json", "abl_nodedupe.json"),
        ("目标函数改为归一化相对收益", "tuned_norm20.json", "norm20.json"),
    ]:
        tun, rec = js(tuned_file), js(rec_file)
        if not (tun and rec):
            continue
        c = compare(base, tun)
        abl.append({"label": label, "n_indexes": rec["n_selected"], "used_mb": rec["used_mb"],
                    "planner_red_pct": rec["cost_reduction_pct"],
                    "speedup": c["speedup"], "geomean": c["geomean"]})
    data["rq4"] = {"rows": abl}

    # ---------------- RQ5: execution-feedback ranking
    ex = js("exec20.json")
    if ex:
        data["rq5"] = {
            "selected": ex["indices"],
            "measured": ex["measured"],
            "candidates": sorted(ex["candidates"],
                                 key=lambda c: -c["measured_gain_ms"]),
        }

    (OUT / "report_data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                          encoding="utf-8")

    # ---------------- figures
    if prof.get("20", {}).get("points"):
        pts = prof["20"]["points"]
        rho = prof["20"]["spearman"]
        fig, ax = plt.subplots(figsize=(5.6, 4.4))
        xs = [p["cost_ratio"] for p in pts]
        ys = [p["speedup"] for p in pts]
        ax.scatter(xs, ys, s=30, color="#2980b9")
        for p in pts:
            ax.annotate(f"Q{p['q']}", (p["cost_ratio"], p["speedup"]), fontsize=7,
                        xytext=(3, 3), textcoords="offset points")
        ax.axhline(1.0, color="#888", lw=0.8, ls="--")
        ax.set_xlabel("planner cost ratio (tuned / baseline)")
        ax.set_ylabel("measured speedup")
        ax.set_title(f"Cost proxy vs measured gain (Spearman {rho:.3f})")
        fig.tight_layout()
        fig.savefig(FIG / "planner_vs_measured.png", dpi=160)
        plt.close(fig)

    if rows:
        fig, ax = plt.subplots(figsize=(5.6, 4))
        mb = [r["used_mb"] for r in rows]
        ax.plot(mb, [r["speedup"] for r in rows], "o-", label="total time speedup")
        ax.plot(mb, [r["geomean"] for r in rows], "s--", label="geometric-mean speedup")
        ax.axhline(1.0, color="#888", lw=0.8, ls=":")
        for r in rows:
            ax.annotate(f"{r['budget_pct']}%", (r["used_mb"], r["speedup"]),
                        textcoords="offset points", xytext=(0, -14), fontsize=8)
        ax.set_xlabel("index storage used (MB)")
        ax.set_ylabel("measured speedup")
        ax.set_title("Budget vs measured benefit (TPC-H SF1)")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(FIG / "budget_curve.png", dpi=160)
        plt.close(fig)

    labels, vals = [], []
    if data.get("rq1"):
        labels.append("baseline"); vals.append(data["rq1"]["total_base_ms"] / 1000)
        labels.append("greedy@20%"); vals.append(data["rq1"]["total_ms"] / 1000)
    if data.get("rq5"):
        labels.append("exec-feedback"); vals.append(data["rq5"]["measured"]["final_ms"] / 1000)
    if labels:
        fig, ax = plt.subplots(figsize=(4.6, 4))
        ax.bar(labels, vals, color=["#7f8c8d", "#2980b9", "#27ae60"][:len(vals)])
        for i, v in enumerate(vals):
            ax.text(i, v, f"{v:,.1f}s", ha="center", va="bottom", fontsize=9)
        ax.set_ylabel("total workload time (s)")
        ax.set_title("Planner-cost vs execution-feedback selection")
        fig.tight_layout()
        fig.savefig(FIG / "method_compare.png", dpi=160)
        plt.close(fig)

    print(json.dumps({k: (v if k in ("rq2", "rq4") else "...") for k, v in data.items()},
                     ensure_ascii=False, indent=2))
    print("wrote", OUT / "report_data.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
