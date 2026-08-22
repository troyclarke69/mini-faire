"""Shared DuckDB read-only query helper for api/metrics_api.py and
api/realtime_api.py. Split out into its own module so realtime_api.py
(PHASE4-REALTIME&STREAMING.md Section 5) can reuse the exact same query
logic without importing api/metrics_api.py itself - metrics_api.py mounts
realtime_api's router, so the reverse import would be circular.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException

from ingestion.duckdb_utils import connect_with_retry
from ingestion.paths import DUCKDB_PATH


def to_json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def query(sql: str, params: list[Any] | None = None) -> list[dict]:
    if not DUCKDB_PATH.exists():
        raise HTTPException(status_code=404, detail="Warehouse not built. Run scripts/run_demo.py first.")
    with connect_with_retry(DUCKDB_PATH, read_only=True) as con:
        result = con.execute(sql, params) if params is not None else con.execute(sql)
        columns = [column[0] for column in result.description]
        return [
            {column: to_json_value(value) for column, value in zip(columns, row)}
            for row in result.fetchall()
        ]


def query_safe(sql: str, params: list[Any] | None = None) -> list[dict]:
    """Like query(), but returns [] instead of raising. Used by the realtime
    WebSocket/SSE polling loop (api/realtime_api.py), where a
    warehouse-not-built or table-not-yet-created condition (e.g. right after
    a fresh checkout, before scripts/run_demo.py has ever run) should
    degrade to "no updates yet" instead of tearing down every open
    connection."""
    try:
        return query(sql, params)
    except Exception:
        return []
