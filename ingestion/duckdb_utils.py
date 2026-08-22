"""Resilient DuckDB connection helper for Phase 4's concurrent writers.

DuckDB allows many concurrent read-only connections, but a write connection
takes an exclusive lock on the database file for its entire lifetime, and no
other connection - read or write - can open while that lock is held. Before
Phase 4, exactly one process ever touched mini_faire.duckdb at a time
(scripts/run_demo.py, orchestration/synthetic_flow.py, orchestration/mongo_flow.py
each ran to completion and exited). Phase 4 introduced several long-lived
processes that can all want a connection around the same moment:
api/metrics_api.py's server, synthetic/stream_generator.py, ingestion/
mongo_change_stream.py, and orchestration/realtime_flow.py.

Every writer in this codebase only holds its write lock for the duration of
one `with duckdb.connect(...) as con: ...` block - a single ingest run, ELT
rebuild, or compute-metrics persist, typically well under a second - so a
"Cannot open file ... The process cannot access the file because it is being
used by another process" collision between two of these is expected to be
transient, not a real conflict. Retrying with a short backoff turns that into
a brief, invisible pause instead of crashing whichever process lost the race,
which is what happened without this: two writers landing at the same instant
took one of them down with an unhandled IOException.

This does NOT eliminate lock contention (DuckDB is still single-writer), it
just makes waiting-your-turn the default behavior instead of a crash.
"""

from __future__ import annotations

import time
from pathlib import Path

import duckdb


def connect_with_retry(
    db_path: Path,
    *,
    read_only: bool = False,
    retries: int = 8,
    initial_delay: float = 0.25,
    max_delay: float = 3.0,
) -> duckdb.DuckDBPyConnection:
    """Like duckdb.connect(), but retries with exponential backoff on
    IOException (file locked by another connection) instead of raising
    immediately. Raises the last IOException if every attempt fails - by
    default that's up to ~1000ms + 2000ms + ... capped at max_delay per
    step, roughly 10-15s worst case across 8 attempts, which comfortably
    outlasts any single ingest/rebuild/compute cycle in this codebase."""
    delay = initial_delay
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return duckdb.connect(str(db_path), read_only=read_only)
        except duckdb.IOException as exc:
            last_exc = exc
            if attempt == retries - 1:
                break
            time.sleep(delay)
            delay = min(delay * 2, max_delay)
    assert last_exc is not None
    raise last_exc
