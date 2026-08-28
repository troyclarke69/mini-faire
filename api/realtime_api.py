"""Real-time API layer (PHASE4-REALTIME&STREAMING.md Section 5).

Exposes the same DuckDB-backed metadata that api/metrics_api.py already
serves over REST (ingestion_runs, elt_model_runs, marts.compute_model_runs,
lineage_edges), but pushed to the frontend as soon as it changes instead of
waiting for the next poll/revalidate. Since the whole pipeline is DuckDB +
filesystem based with no message bus, "pushed" here means: each connection
polls the warehouse on a short interval, tracks the newest timestamp it has
already sent per topic, and only sends rows newer than that - a diff, not a
full re-send.

Both a WebSocket endpoint (Section A) and an SSE endpoint (Section B) serve
the same payload shape so a client can use whichever integrates more simply
(frontend/lib/realtime.ts uses WebSocket, with the SSE endpoint available as
a fallback/alternative). /realtime/health (Section C) reports whether the
three standalone streaming services (synthetic/stream_generator.py,
ingestion/mongo_change_stream.py, orchestration/realtime_flow.py) are
actually running, via the filesystem heartbeats they each write
(ingestion/heartbeat.py) - they're separate OS processes, so there's no
in-process object this API server could otherwise ask.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from api.db import query_safe
from ingestion.heartbeat import heartbeat_status
from ingestion.paths import DUCKDB_PATH

router = APIRouter(prefix="/realtime", tags=["realtime"])

POLL_INTERVAL_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 20.0  # keep-alive message when nothing changed

# topic -> (source table/view, ORDER/WHERE timestamp column, row limit per poll)
_TOPICS: dict[str, tuple[str, str, int]] = {
    "ingestion_runs": ("ingestion_runs", "completed_at", 20),
    "elt_model_runs": ("elt_model_runs", "completed_at", 20),
    "compute_model_runs": ("marts.compute_model_runs", "computed_at", 20),
    "lineage_edges": ("lineage_edges", "created_at", 50),
}


async def _fetch_topic(table: str, ts_column: str, limit: int, since: str | None) -> list[dict[str, Any]]:
    if since is None:
        sql = f"select * from {table} order by {ts_column} desc limit {limit}"
        params = None
    else:
        sql = f"select * from {table} where {ts_column} > ? order by {ts_column} desc limit {limit}"
        params = [since]
    return await asyncio.to_thread(query_safe, sql, params)


async def _poll_delta(cursors: dict[str, str | None]) -> dict[str, Any]:
    """One sweep across every topic. Updates `cursors` in place (to the
    newest timestamp seen so far per topic) and returns only what's new."""
    changed: dict[str, list[dict[str, Any]]] = {}
    for topic, (table, ts_column, limit) in _TOPICS.items():
        rows = await _fetch_topic(table, ts_column, limit, cursors[topic])
        if rows:
            changed[topic] = rows
            newest = rows[0].get(ts_column)  # rows are ordered desc
            if newest:
                cursors[topic] = newest
    return changed


def _new_cursors() -> dict[str, str | None]:
    return {topic: None for topic in _TOPICS}


def _envelope(changed: dict[str, Any], *, kind: str) -> dict[str, Any]:
    return {"type": kind, "server_time": datetime.now(UTC).isoformat(), **changed}


# ---------------------------------------------------------------------------
# A. WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def realtime_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    cursors = _new_cursors()

    # Everything below - including the initial snapshot send - is inside
    # this try/except. The initial send used to sit before the try, which
    # meant a client that disconnected between accept() and that first
    # send (e.g. React StrictMode's dev-mode double-mount, which opens and
    # immediately tears down a socket) crashed the ASGI app instead of
    # exiting quietly - the same "the send discovers the disconnect, not
    # WebSocketDisconnect" gap `except Exception` below also covers.
    try:
        # Initial snapshot so a freshly-connected client doesn't have to
        # wait a full poll interval (or miss everything that happened
        # before it connected) to see the latest state.
        initial = await _poll_delta(cursors)
        await websocket.send_json(_envelope(initial, kind="snapshot"))

        last_sent_at = asyncio.get_event_loop().time()
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            changed = await _poll_delta(cursors)
            now = asyncio.get_event_loop().time()
            if changed:
                await websocket.send_json(_envelope(changed, kind="update"))
                last_sent_at = now
            elif now - last_sent_at >= HEARTBEAT_INTERVAL_SECONDS:
                await websocket.send_json(_envelope({}, kind="heartbeat"))
                last_sent_at = now
    except WebSocketDisconnect:
        return
    except Exception:
        # A send against an already-closed socket can surface as something
        # other than WebSocketDisconnect depending on exactly when/how the
        # client went away (e.g. uvicorn's own ClientDisconnected, raised
        # from send() rather than receive() - not a class Starlette
        # re-exports, so not worth importing just to name it here). Nothing
        # in this loop has a side effect worth distinguishing a real bug
        # from a vanished client for, so any exception at this point just
        # means "stop pushing," same as a clean WebSocketDisconnect.
        return


# ---------------------------------------------------------------------------
# B. SSE endpoint (simpler client integration - plain EventSource, no extra
# library needed either side: FastAPI's StreamingResponse handles the
# `text/event-stream` framing directly).
# ---------------------------------------------------------------------------


async def _sse_event_source():
    cursors = _new_cursors()
    initial = await _poll_delta(cursors)
    yield f"data: {json.dumps(_envelope(initial, kind='snapshot'))}\n\n"

    last_sent_at = asyncio.get_event_loop().time()
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        changed = await _poll_delta(cursors)
        now = asyncio.get_event_loop().time()
        if changed:
            yield f"data: {json.dumps(_envelope(changed, kind='update'))}\n\n"
            last_sent_at = now
        elif now - last_sent_at >= HEARTBEAT_INTERVAL_SECONDS:
            yield f"data: {json.dumps(_envelope({}, kind='heartbeat'))}\n\n"
            last_sent_at = now


@router.get("/stream")
async def realtime_stream() -> StreamingResponse:
    return StreamingResponse(
        _sse_event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# C. Health check
# ---------------------------------------------------------------------------


@router.get("/health")
def realtime_health() -> dict[str, Any]:
    services = {
        "stream_generator": heartbeat_status("stream_generator"),
        "mongo_change_stream": heartbeat_status("mongo_change_stream"),
        "realtime_flow": heartbeat_status("realtime_flow"),
    }
    return {
        "status": "ok" if DUCKDB_PATH.exists() else "warehouse_missing",
        "warehouse_path": str(DUCKDB_PATH),
        "services": services,
        "any_streaming_service_running": any(s["status"] == "running" for s in services.values()),
    }
