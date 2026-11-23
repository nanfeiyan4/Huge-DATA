"""Thin PostgreSQL access layer (psycopg3).

Kept deliberately small so the advisor code never touches driver details.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass


@dataclass
class DBConfig:
    host: str = "127.0.0.1"
    port: int = 5499
    dbname: str = "idxlab"
    user: str = "postgres"
    password: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "DBConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class QueryTimeout(Exception):
    pass


def _strip(sql: str) -> str:
    """PostgreSQL rejects trailing semicolons inside subqueries."""
    s = sql.strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


class DB:
    def __init__(self, cfg: DBConfig, vendor: str | None = None):
        import sys

        if vendor:
            sys.path.insert(0, vendor)
        import psycopg

        self._psycopg = psycopg
        self.conn = psycopg.connect(
            host=cfg.host, port=cfg.port, dbname=cfg.dbname, user=cfg.user,
            password=cfg.password or None, autocommit=True,
            options="-c statement_timeout=0",
        )
        self.cur = self.conn.cursor()

    # ---------------------------------------------------------------- basics
    def q(self, sql: str, params=None) -> list[tuple]:
        self.cur.execute(sql, params)
        return self.cur.fetchall()

    def q1(self, sql: str, params=None):
        rows = self.q(sql, params)
        return rows[0][0] if rows else None

    def execute(self, sql: str) -> None:
        self.cur.execute(sql)

    def close(self) -> None:
        try:
            self.cur.close()
            self.conn.close()
        except Exception:
            pass

    # -------------------------------------------------------------- planning
    def explain_cost(self, sql: str) -> float:
        self.cur.execute("EXPLAIN (FORMAT JSON) " + _strip(sql))
        plan = self.cur.fetchone()[0]
        if isinstance(plan, str):
            plan = json.loads(plan)
        return float(plan[0]["Plan"]["Total Cost"])

    # ----------------------------------------------------------- measurement
    def timed(self, sql: str, timeout_ms: int = 300_000) -> tuple[float, list[tuple]]:
        """Run ``sql`` to completion; return (milliseconds, rows)."""
        sql = _strip(sql)
        self.cur.execute(f"SET statement_timeout = {int(timeout_ms)}")
        t0 = time.perf_counter()
        try:
            self.cur.execute(sql)
            rows = self.cur.fetchall()
        except self._psycopg.errors.QueryCanceled as exc:  # type: ignore[attr-defined]
            raise QueryTimeout(sql[:80]) from exc
        finally:
            self.cur.execute("SET statement_timeout = 0")
        return (time.perf_counter() - t0) * 1000.0, rows

    def result_digest(self, sql: str) -> str:
        """Content hash of a query result, insensitive to row order."""
        sql = _strip(sql)
        wrapped = (
            "SELECT md5(COALESCE(string_agg(r, '|' ORDER BY r), '')) "
            f"FROM (SELECT to_jsonb(x)::text AS r FROM ({sql}) x) s"
        )
        self.cur.execute("SET statement_timeout = 0")
        self.cur.execute(wrapped)
        return self.cur.fetchone()[0]
