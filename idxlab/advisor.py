"""Index advisor core: candidate generation, size estimation, cost models,
budget-constrained greedy selection with incremental re-estimation.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .workload import Query

MAX_COLS = 4


# --------------------------------------------------------------------------- #
# candidates
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Candidate:
    table: str
    columns: tuple[str, ...]
    source: str = "rule"

    def key(self) -> tuple[str, tuple[str, ...]]:
        return (self.table, self.columns)

    @property
    def key_str(self) -> str:
        return self.table + "_" + "_".join(self.columns)

    def hypo_sql(self) -> str:
        return f"CREATE INDEX ON {self.table} ({', '.join(self.columns)})"

    def real_sql(self, name: str | None = None) -> str:
        return f"CREATE INDEX {name or 'ix_' + self.key_str} ON {self.table} ({', '.join(self.columns)})"


def _pairs(items):
    """All unordered index pairs of ``items`` (indices), excluding self-pairs."""
    items = list(items)
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            yield items[i], items[j]


def generate_candidates(
    queries: Sequence[Query],
    schema: dict[str, set[str]],
    stats: dict[str, "TableStat"] | None = None,
    max_cols: int = MAX_COLS,
    source: str = "rule",
) -> list[Candidate]:
    """Rule-based candidate pool: equality prefix + optional range tail,
    single-column join indexes, and join/eq combinations."""
    pool: list[Candidate] = []

    def order_cols(table: str, cols: Iterable[str]) -> tuple[str, ...]:
        cols = list(dict.fromkeys(cols))
        if stats and table in stats and stats[table].ndistinct:
            st = stats[table]
            cols.sort(key=lambda c: -st.ndistinct_ratio(c))
        else:
            cols.sort()
        return tuple(cols[:max_cols])

    for q in queries:
        pred_tables = {t for (t, _c) in q.eq_preds | q.range_preds | q.join_cols}
        for table in sorted(set(q.relations.values()) | pred_tables):
            valid = schema.get(table)
            if not valid:
                continue
            eq = [c for (t, c) in sorted(q.eq_preds) if t == table and c in valid]
            rng = [c for (t, c) in sorted(q.range_preds) if t == table and c in valid and c not in eq]
            joins = [c for (t, c) in sorted(q.join_cols) if t == table and c in valid
                     and c not in eq and c not in rng]
            tail = [c for (t, c) in sorted(q.group_cols | q.order_cols) if t == table
                    and c in valid and c not in eq and c not in rng]

            eq_cols = order_cols(table, eq)
            if eq_cols:
                pool.append(Candidate(table, eq_cols, source))
                # 2-column prefixes of the equality set (different leading column)
                if len(eq_cols) >= 3:
                    for i, j in list(_pairs(range(len(eq_cols))))[:6]:
                        pool.append(Candidate(table, order_cols(table, [eq_cols[i], eq_cols[j]]), source))
            if rng:
                pool.append(Candidate(table, order_cols(table, rng[:1]), source))
            if eq_cols and rng:
                pool.append(Candidate(table, order_cols(table, list(eq_cols) + rng[:1]), source))
            for j in joins:
                pool.append(Candidate(table, (j,), source))
                if eq_cols:
                    pool.append(Candidate(table, order_cols(table, [j] + list(eq_cols)), source))
                    pool.append(Candidate(table, order_cols(table, list(eq_cols) + [j]), source))
            # join-column pairs: the workhorses of JOB-style star joins
            for j1, j2 in list(_pairs(joins))[:4]:
                pool.append(Candidate(table, (j1, j2), source))
                if eq_cols:
                    pool.append(Candidate(table, order_cols(table, [j1, j2] + list(eq_cols[:1])), source))
                    pool.append(Candidate(table, order_cols(table, list(eq_cols[:1]) + [j1, j2]), source))
            if tail and eq_cols:
                pool.append(Candidate(table, order_cols(table, list(eq_cols) + tail[:1]), source))
            elif tail:
                pool.append(Candidate(table, order_cols(table, tail[:1]), source))

    seen: set[tuple] = set()
    uniq: list[Candidate] = []
    for c in pool:
        if c.key() in seen:
            continue
        seen.add(c.key())
        uniq.append(c)
    return uniq


def drop_prefix_covered(cands: Sequence[Candidate]) -> list[Candidate]:
    """Remove (a) when some (a, b, ...) exists: btree prefixes already serve it."""
    out = []
    for c in cands:
        covered = any(
            o.table == c.table
            and len(o.columns) > len(c.columns)
            and o.columns[: len(c.columns)] == c.columns
            for o in cands
        )
        if not covered:
            out.append(c)
    return out


def merge_candidates(*pools: Sequence[Candidate]) -> list[Candidate]:
    seen: set[tuple] = set()
    out: list[Candidate] = []
    for pool in pools:
        for c in pool:
            if c.key() in seen:
                continue
            seen.add(c.key())
            out.append(c)
    return out


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #
@dataclass
class TableStat:
    name: str
    rows: float
    width: dict[str, int] = field(default_factory=dict)
    ndistinct: dict[str, float] = field(default_factory=dict)  # absolute counts

    def ndistinct_ratio(self, col: str) -> float:
        if not self.rows:
            return 0.0
        return self.ndistinct.get(col, 0.0) / max(self.rows, 1.0)


def load_stats(db) -> dict[str, TableStat]:
    stats: dict[str, TableStat] = {}
    for name, rows in db.q(
        "SELECT c.relname, c.reltuples FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
    ):
        stats[name] = TableStat(name=name, rows=float(rows or 0))
    for table, att, avg_width, ndist in db.q(
        "SELECT tablename, attname, avg_width, n_distinct FROM pg_stats WHERE schemaname = 'public'"
    ):
        st = stats.get(table)
        if st is None:
            continue
        st.width[att] = int(avg_width or 32)
        if ndist is not None and ndist < 0:
            st.ndistinct[att] = abs(float(ndist)) * max(st.rows, 1.0)
        else:
            st.ndistinct[att] = float(ndist or 0)
    return stats


def estimate_index_bytes(stat: TableStat, columns: Sequence[str]) -> int:
    """B-tree size estimate: row width + tuple header, fillfactor 0.9, ~5% overhead."""
    rows = max(stat.rows, 1.0)
    width = sum(stat.width.get(c, 32) for c in columns)
    tuple_bytes = width + 16  # heap TID + header + alignment slack
    return int(rows * tuple_bytes * 1.05 / 0.90)


def index_sizes(cands: Sequence[Candidate], stats: dict[str, TableStat]) -> dict[tuple, int]:
    out = {}
    for c in cands:
        st = stats.get(c.table)
        out[c.key()] = estimate_index_bytes(st, c.columns) if st else 0
    return out


# --------------------------------------------------------------------------- #
# cost models
# --------------------------------------------------------------------------- #
class CostModel:
    def set_active(self, cands: Sequence[Candidate]) -> None:
        raise NotImplementedError

    def costs(self, qids: Sequence[str]) -> dict[str, float]:
        raise NotImplementedError

    @property
    def evals(self) -> int:
        return getattr(self, "_evals", 0)


class HypoPGModel(CostModel):
    """Planner-cost oracle using the hypopg extension (one session, many what-ifs)."""

    def __init__(self, db, queries: dict[str, str]):
        self.db = db
        self.sql = queries
        self._active: dict[tuple, int] = {}
        self._cache: dict[str, float] = {}
        self._evals = 0
        if not self.db.q1("SELECT count(*) FROM pg_extension WHERE extname = 'hypopg'"):
            raise RuntimeError("hypopg extension is not installed in this database "
                               "(CREATE EXTENSION hypopg;)")

    def set_active(self, cands: Sequence[Candidate]) -> None:
        want = {c.key(): c for c in cands}
        for key in list(self._active):
            if key not in want:
                self.db.q("SELECT hypopg_drop_index(%s)", (self._active.pop(key),))
        for key, cand in want.items():
            if key not in self._active:
                oid = self.db.q1("SELECT indexrelid FROM hypopg_create_index(%s)",
                                 (cand.hypo_sql(),))
                self._active[key] = oid
        self._cache.clear()

    def costs(self, qids: Sequence[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for qid in qids:
            if qid not in self._cache:
                self._cache[qid] = self.db.explain_cost(self.sql[qid])
                self._evals += 1
            out[qid] = self._cache[qid]
        return out


class RealIndexModel(CostModel):
    """Fallback oracle: builds and drops real indexes (slow, but no extension)."""

    def __init__(self, db, queries: dict[str, str]):
        self.db = db
        self.sql = queries
        self._active: dict[tuple, str] = {}
        self._cache: dict[str, float] = {}
        self._evals = 0

    @staticmethod
    def _name(cand: Candidate) -> str:
        return "ix_" + cand.key_str

    def set_active(self, cands: Sequence[Candidate]) -> None:
        want = {c.key(): c for c in cands}
        for key in list(self._active):
            if key not in want:
                self.db.execute(f"DROP INDEX IF EXISTS {self._active.pop(key)}")
        for key, cand in want.items():
            if key not in self._active:
                self.db.execute(cand.real_sql(self._name(cand)))
                self._active[key] = self._name(cand)
        self._cache.clear()

    def costs(self, qids: Sequence[str]) -> dict[str, float]:
        out = {}
        for qid in qids:
            if qid not in self._cache:
                self.db.execute("ANALYZE")
                self._cache[qid] = self.db.explain_cost(self.sql[qid])
                self._evals += 1
            out[qid] = self._cache[qid]
        return out


class MockModel(CostModel):
    """Deterministic synthetic cost model; only for unit-testing the search."""

    def __init__(self, queries: dict[str, Query]):
        self.queries = queries
        self._active: list[Candidate] = []
        self._evals = 0
        self.base = {qid: 1000.0 + 100.0 * (idx % 17) for idx, qid in enumerate(sorted(queries))}

    def set_active(self, cands: Sequence[Candidate]) -> None:
        self._active = list(cands)

    def costs(self, qids: Sequence[str]) -> dict[str, float]:
        out = {}
        for qid in qids:
            self._evals += 1
            q = self.queries[qid]
            factor = 1.0
            for table in q.tables:
                eq = sorted({c for (t, c) in q.eq_preds if t == table})
                best = 0.0
                for cand in self._active:
                    if cand.table != table:
                        continue
                    k = 0
                    for col in cand.columns:
                        if col in eq:
                            k += 1
                        else:
                            break
                    best = max(best, k / max(len(eq), 1))
                factor *= 1.0 - 0.75 * best
            out[qid] = self.base[qid] * max(factor, 0.05)
        return out


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #
@dataclass
class Step:
    iteration: int
    candidate: Candidate
    gain: float
    ratio: float
    size_bytes: int
    cost_after: float


@dataclass
class Selection:
    selected: list[Candidate]
    steps: list[Step]
    base_cost: float
    final_cost: float
    used_bytes: int
    evals: int
    budget_bytes: int

    def summary(self) -> dict:
        return {
            "n_selected": len(self.selected),
            "used_bytes": self.used_bytes,
            "used_mb": round(self.used_bytes / 2**20, 2),
            "budget_mb": round(self.budget_bytes / 2**20, 2),
            "base_cost": round(self.base_cost, 1),
            "final_cost": round(self.final_cost, 1),
            "cost_reduction_pct": round(100 * (1 - self.final_cost / max(self.base_cost, 1e-9)), 2),
            "evals": self.evals,
            "indexes": [
                {"table": c.table, "columns": list(c.columns), "source": c.source}
                for c in self.selected
            ],
        }


def _gain(cur: dict[str, float], new: dict[str, float], base: dict[str, float],
          objective: str) -> float:
    """Aggregate improvement over the affected queries.

    ``raw``        : sum of planner cost reduction (dominated by expensive queries)
    ``normalized`` : mean of per-query *relative* cost reduction (each query counts once)
    """
    if objective == "normalized":
        return sum((cur[q] - new[q]) / max(base.get(q, 1.0), 1e-9) for q in new)
    return sum(cur[q] - new[q] for q in new)


def standalone_gains(model: CostModel, cands: Sequence[Candidate], qids: Sequence[str],
                     affected: dict[str, list[str]],
                     objective: str = "raw") -> dict[tuple, float]:
    """Cost reduction each candidate achieves on its own (used for prescreening
    and as the "no re-estimation" ablation baseline)."""
    model.set_active([])
    cur = model.costs(list(qids))
    base = dict(cur)
    gains: dict[tuple, float] = {}
    for cand in cands:
        model.set_active([cand])
        new = model.costs(affected.get(cand.table, []))
        gains[cand.key()] = _gain(cur, new, base, objective)
    model.set_active([])
    return gains


def greedy_select(
    model: CostModel,
    cands: Sequence[Candidate],
    sizes: dict[tuple, int],
    budget_bytes: int,
    qids: Sequence[str],
    affected: dict[str, list[str]],
    restate: bool = True,
    kswap_rounds: int = 1,
    objective: str = "raw",
    log: Callable[[str], None] | None = None,
) -> Selection:
    """Budget-constrained greedy forward selection.

    ``restate``: re-estimate marginal gains of all remaining candidates after
    every accepted step (True) or keep the initial standalone gains (False,
    used as an ablation).
    """
    qids = list(qids)
    model.set_active([])
    cur = model.costs(qids)
    base_per_query = dict(cur)
    base_total = sum(cur.values())
    active: list[Candidate] = []
    active_keys: set[tuple] = set()
    steps: list[Step] = []
    used = 0

    standalone: dict[tuple, float] = {}
    if not restate:
        for cand in cands:
            model.set_active([cand])
            new = model.costs(affected.get(cand.table, []))
            standalone[cand.key()] = _gain(cur, new, base_per_query, objective)
        model.set_active([])

    for it in range(1, 65):
        best: tuple[float, Candidate, float] | None = None
        for cand in cands:
            if cand.key() in active_keys:
                continue
            size = sizes.get(cand.key(), 0)
            if used + size > budget_bytes:
                continue
            if not restate:
                gain = standalone.get(cand.key(), 0.0)
            else:
                model.set_active(active + [cand])
                new = model.costs(affected.get(cand.table, []))
                gain = _gain(cur, new, base_per_query, objective)
            if gain <= 1e-9 or size <= 0:
                continue
            ratio = gain / size
            if best is None or ratio > best[0]:
                best = (ratio, cand, gain)
        if best is None:
            break
        ratio, cand, gain = best
        model.set_active(active + [cand])
        new = model.costs(affected.get(cand.table, []))
        cur.update(new)
        active.append(cand)
        active_keys.add(cand.key())
        used += sizes.get(cand.key(), 0)
        steps.append(Step(it, cand, gain, ratio, sizes.get(cand.key(), 0), sum(cur.values())))
        if log:
            log(f"  +{it:02d} {cand.table}({','.join(cand.columns)}) "
                f"gain={gain:,.0f} size={sizes.get(cand.key(), 0)/2**20:.1f}MB")

    selected = list(active)
    for _ in range(max(kswap_rounds, 0)):
        improved = _one_swap_round(model, selected, cands, sizes, budget_bytes,
                                   affected, cur, qids, base_per_query, objective)
        if not improved:
            break

    model.set_active(selected)
    final = model.costs(qids)
    return Selection(
        selected=selected, steps=steps, base_cost=base_total,
        final_cost=sum(final.values()), used_bytes=sum(sizes.get(c.key(), 0) for c in selected),
        evals=model.evals, budget_bytes=budget_bytes,
    )


def _one_swap_round(model, selected, cands, sizes, budget_bytes, affected, cur, qids,
                    base_per_query=None, objective="raw") -> bool:
    """Try removing one selected index and adding another; apply the first
    improving swap in place (``selected`` is mutated)."""
    used = sum(sizes.get(c.key(), 0) for c in selected)
    sel_keys = {c.key() for c in selected}
    for out_idx in range(len(selected)):
        out = selected[out_idx]
        for cand in cands:
            if cand.key() in sel_keys:
                continue
            new_used = used - sizes.get(out.key(), 0) + sizes.get(cand.key(), 0)
            if new_used > budget_bytes:
                continue
            trial = [c for i, c in enumerate(selected) if i != out_idx] + [cand]
            model.set_active(trial)
            tq = sorted(set(affected.get(out.table, [])) | set(affected.get(cand.table, [])))
            new = model.costs(tq)
            delta = _gain(cur, new, base_per_query or {}, objective)
            if delta > 1e-6:
                cur.update(new)
                selected[:] = trial
                return True
    return False
