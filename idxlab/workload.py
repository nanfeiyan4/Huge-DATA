"""Parsing utilities for SQL workloads (JOB / TPC-H style single-statement SELECTs).

The parser is deliberately heuristic: it extracts relations, predicate columns,
join columns and GROUP/ORDER columns, which is all the index advisor needs.
Authoritative column names are later validated against the database catalog.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_COMMENT = re.compile(r"/\*.*?\*/|--[^\n]*", re.S)
_WS = re.compile(r"\s+")

_ALIAS_STOP = {
    "where", "group", "order", "limit", "on", "join", "inner", "left", "right",
    "full", "cross", "using", "having", "union", "as", "select", "from", "and", "or",
    "not", "is", "null", "in",
}

_CLAUSE_PATTERNS = [
    ("from", re.compile(r"\bfrom\b", re.I)),
    ("where", re.compile(r"\bwhere\b", re.I)),
    ("group_by", re.compile(r"\bgroup\s+by\b", re.I)),
    ("order_by", re.compile(r"\border\s+by\b", re.I)),
    ("limit", re.compile(r"\blimit\b", re.I)),
]

_PRED = re.compile(
    r"\b([a-z_][\w]*)\.([a-z_][\w]*)\s*(<=|>=|<>|!=|=|<|>|ilike|like|between)\s*"
    r"([a-z_][\w]*\.[a-z_][\w]*|[^()\s,]+)",
    re.I,
)

# Unqualified predicates (TPC-H style: ``l_shipdate <= date '1998-12-01'``)
_BARE_PRED = re.compile(
    r"(?<![.\w])([a-z_][\w]*)\s*(<=|>=|<>|!=|=|<|>|ilike|like|between)\s*"
    r"([a-z_][\w]*\.[a-z_][\w]*|date\s*'[^']*'|[^()\s,]+)",
    re.I,
)

_CREATE_TABLE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?[\"']?([a-z_][\w]*)[\"']?\s*\((.*?)\)\s*;",
    re.I | re.S,
)


@dataclass
class Query:
    qid: str
    sql: str
    relations: dict[str, str] = field(default_factory=dict)
    eq_preds: set[tuple[str, str]] = field(default_factory=set)
    range_preds: set[tuple[str, str]] = field(default_factory=set)
    join_cols: set[tuple[str, str]] = field(default_factory=set)
    group_cols: set[tuple[str, str]] = field(default_factory=set)
    order_cols: set[tuple[str, str]] = field(default_factory=set)

    @property
    def tables(self) -> set[str]:
        return set(self.relations.values())

    def to_dict(self) -> dict:
        return {
            "qid": self.qid,
            "relations": self.relations,
            "eq_preds": sorted(self.eq_preds),
            "range_preds": sorted(self.range_preds),
            "join_cols": sorted(self.join_cols),
            "group_cols": sorted(self.group_cols),
            "order_cols": sorted(self.order_cols),
        }


def clean(sql: str) -> str:
    sql = _COMMENT.sub(" ", sql)
    return _WS.sub(" ", sql.replace("\r", " ")).strip()


def _split_top_level(s: str, pattern: str) -> list[str]:
    """Split ``s`` on regex ``pattern`` matches that occur at paren depth 0."""
    rx = re.compile(pattern, re.I)
    out, last, depth, i = [], 0, 0, 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0:
            m = rx.match(s, i)
            if m and m.end() > i:
                out.append(s[last:i])
                last = i = m.end()
                continue
        i += 1
    out.append(s[last:])
    return [p.strip() for p in out if p.strip()]


def _clauses(sql: str) -> dict[str, str]:
    """Return the body of each top-level clause."""
    positions: dict[str, int] = {}
    depth, i, n = 0, 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0:
            hit = False
            for name, pat in _CLAUSE_PATTERNS:
                m = pat.match(sql, i)
                if m:
                    positions.setdefault(name, m.start())
                    i = m.end()
                    hit = True
                    break
            if hit:
                continue
        i += 1
    ordered = sorted(positions.items(), key=lambda kv: kv[1])
    bodies: dict[str, str] = {}
    for idx, (name, start) in enumerate(ordered):
        end = ordered[idx + 1][1] if idx + 1 < len(ordered) else len(sql)
        body = sql[start:end]
        body = re.sub(r"^\S+(\s+by)?\s*", "", body, count=1).strip()
        bodies[name] = body
    return bodies


def _parse_relations(from_body: str) -> dict[str, str]:
    rels: dict[str, str] = {}
    chunks: list[str] = []
    for part in _split_top_level(from_body, r","):
        chunks.extend(_split_top_level(part, r"\b(?:inner|left|right|full|cross)\s+join\b|\bjoin\b"))
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk or chunk.startswith("("):
            continue
        m = re.match(r"([a-z_][\w]*)(?:\s+(?:as\s+)?([a-z_][\w]*))?", chunk, re.I)
        if not m:
            continue
        table = m.group(1).lower()
        alias = (m.group(2) or table).lower()
        if alias in _ALIAS_STOP:
            alias = table
        rels[alias] = table
    return rels


def _col_refs(body: str, rels: dict[str, str]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for alias, col in re.findall(r"\b([a-z_][\w]*)\.([a-z_][\w]*)\b", body, re.I):
        table = rels.get(alias.lower())
        if table:
            out.add((table, col.lower()))
    return out


def parse_query(qid: str, sql: str, schema: dict[str, set[str]] | None = None) -> Query:
    sql = clean(sql)
    bodies = _clauses(sql)
    rels = _parse_relations(bodies.get("from", ""))
    rels = _scan_relations(sql) | rels
    q = Query(qid=qid, sql=sql, relations=rels)

    # Scan the whole statement, not just the outer WHERE: many queries hide their
    # selective predicates inside derived tables / EXISTS subqueries.
    where = sql
    for alias, col, op, rhs in _PRED.findall(where):
        alias, col, op, rhs = alias.lower(), col.lower(), op.lower(), rhs.strip()
        table = rels.get(alias)
        if table is None:
            continue
        rhs_ref = re.match(r"^([a-z_][\w]*)\.([a-z_][\w]*)$", rhs, re.I)
        if rhs_ref and op == "=":
            rtable = rels.get(rhs_ref.group(1).lower())
            if rtable:
                q.join_cols.add((table, col))
                q.join_cols.add((rtable, rhs_ref.group(2).lower()))
                continue
        if op == "=" and not rhs.startswith("("):
            q.eq_preds.add((table, col))
        elif op in ("<", "<=", ">", ">=", "between", "like", "ilike"):
            q.range_preds.add((table, col))
        elif op in ("<>", "!="):
            q.range_preds.add((table, col))

    if schema:
        _parse_bare_preds(q, where, schema)

    q.group_cols |= _col_refs(bodies.get("group_by", ""), rels)
    q.order_cols |= _col_refs(bodies.get("order_by", ""), rels)
    return q


def _scan_relations(sql: str) -> dict[str, str]:
    """Collect relations from every FROM/JOIN in the statement (any depth)."""
    rels: dict[str, str] = {}
    for m in re.finditer(r"\b(?:from|join)\s+([a-z_][\w]*)(?:\s+(?:as\s+)?([a-z_][\w]*))?", sql, re.I):
        table = m.group(1).lower()
        alias = (m.group(2) or table).lower()
        if table in {"select", "lateral"}:
            continue
        if alias in _ALIAS_STOP:
            alias = table
        rels.setdefault(alias, table)
    return rels



def _resolve_bare(col: str, schema: dict[str, set[str]]) -> str | None:
    """Map an unqualified column to its table when the name is unambiguous."""
    hits = [t for t, cols in schema.items() if col in cols]
    return hits[0] if len(hits) == 1 else None


def _parse_bare_preds(q: Query, where: str, schema: dict[str, set[str]]) -> None:
    for col, op, rhs in _BARE_PRED.findall(where):
        col, op = col.lower(), op.lower()
        table = _resolve_bare(col, schema)
        if table is None:
            continue
        rhs = rhs.strip()
        rhs_ref = re.match(r"^([a-z_][\w]*)$", rhs, re.I)
        if rhs_ref and op == "=":
            rtable = _resolve_bare(rhs_ref.group(1).lower(), schema)
            if rtable and rtable != table:
                q.join_cols.add((table, col))
                q.join_cols.add((rtable, rhs_ref.group(1).lower()))
                continue
        if op == "<>" or op == "!=":
            q.range_preds.add((table, col))
        elif op == "=" and rhs not in _SQL_LITERAL_WORDS:
            q.eq_preds.add((table, col))
        elif op in ("<", "<=", ">", ">=", "between", "like", "ilike"):
            q.range_preds.add((table, col))


_SQL_LITERAL_WORDS = {"null", "true", "false", "current_date"}


def load_workload(directory: str | Path, pattern: str = "*.sql",
                  skip: tuple[str, ...] = (),
                  schema: dict[str, set[str]] | None = None) -> list[Query]:
    queries: list[Query] = []
    for path in sorted(Path(directory).glob(pattern)):
        if path.stem in skip:
            continue
        queries.append(parse_query(path.stem, path.read_text(encoding="utf-8", errors="replace"),
                                   schema=schema))
    return queries


def schema_from_ddl(path: str | Path) -> dict[str, set[str]]:
    """Extract ``table -> {columns}`` from a plain ``CREATE TABLE`` DDL file."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    schema: dict[str, set[str]] = {}
    for table, body in _CREATE_TABLE.findall(text):
        cols: set[str] = set()
        for part in _split_top_level(body, r","):
            head = part.split()[0].strip('"').lower() if part.split() else ""
            if not head or head in {"primary", "foreign", "unique", "check", "constraint", "exclude"}:
                continue
            cols.add(head)
        schema[table.lower()] = cols
    return schema
