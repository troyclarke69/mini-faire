"""Monitoring & Alerts API (PHASE5-MONITORING.md Section 5).

REST endpoints expose the four Phase 5 tables (anomalies.anomaly_events,
monitoring.system_metrics, monitoring.schema_drift_events,
monitoring.alert_events) plus two health-check endpoints, all through
api/db.py's query_safe() - identical convention to api/metrics_api.py and
api/realtime_api.py: a missing table (fresh checkout, no anomalies/metrics
recorded yet) degrades to an empty list rather than a 500.

The WebSocket/SSE push endpoints below are a direct copy of
api/realtime_api.py's diff-poll pattern (see that module's docstring for the
full rationale) applied to a different topic set. It is duplicated rather
than generalized into a shared helper because the two routers' topic tables,
prefixes, and envelope shapes differ enough that a shared abstraction would
need almost as many parameters as it saved lines - see
frontend/lib/monitoringRealtime.ts for the equivalent, deliberate duplication
on the client side.

PHASE5-MONITORING.md Section 5 also asks for "streaming lag updates" and
"ingestion latency updates" as their own push categories. Both are already
ordinary rows in monitoring.system_metrics (metric_name
'streaming_lag_seconds' / 'ingestion_latency_avg_ms') written by
monitoring/metrics.py, so they arrive through the existing `system_metrics`
topic below rather than as two more ad-hoc topics - a client that only cares
about one of them filters system_metrics rows by metric_name client-side.
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

router = APIRouter(prefix="/monitoring", tags=["monitoring"])

POLL_INTERVAL_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 20.0

# topic -> (source table, ORDER/WHERE timestamp column, row limit per poll)
_TOPICS: dict[str, tuple[str, str, int]] = {
    "anomalies": ("anomalies.anomaly_events", "detected_at", 20),
    "alerts": ("monitoring.alert_events", "created_at", 20),
    "system_metrics": ("monitoring.system_metrics", "computed_at", 50),
    "schema_drift": ("monitoring.schema_drift_events", "detected_at", 20),
}


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.get("/system-metrics")
def system_metrics() -> list[dict]:
    return query_safe("select * from monitoring.system_metrics order by computed_at desc limit 500")


@router.get("/anomalies")
def anomalies() -> list[dict]:
    return query_safe("select * from anomalies.anomaly_events order by detected_at desc limit 500")


@router.get("/schema-drift")
def schema_drift() -> list[dict]:
    return query_safe("select * from monitoring.schema_drift_events order by detected_at desc limit 500")


@router.get("/alerts")
def alerts() -> list[dict]:
    return query_safe("select * from monitoring.alert_events order by created_at desc limit 500")


def _services_health() -> dict[str, Any]:
    return {
        "stream_generator": heartbeat_status("stream_generator"),
        "mongo_change_stream": heartbeat_status("mongo_change_stream"),
        "realtime_flow": heartbeat_status("realtime_flow"),
    }


@router.get("/health")
def monitoring_health() -> dict[str, Any]:
    services = _services_health()
    recent_anomalies = query_safe(
        "select count(*) as n from anomalies.anomaly_events where detected_at > current_timestamp - interval 1 hour"
    )
    recent_critical = query_safe(
        "select count(*) as n from anomalies.anomaly_events "
        "where severity = 'critical' and detected_at > current_timestamp - interval 1 hour"
    )
    recent_alerts = query_safe(
        "select count(*) as n from monitoring.alert_events where created_at > current_timestamp - interval 1 hour"
    )
    return {
        "status": "ok" if DUCKDB_PATH.exists() else "warehouse_missing",
        "warehouse_path": str(DUCKDB_PATH),
        "services": services,
        "any_streaming_service_running": any(s["status"] == "running" for s in services.values()),
        "anomalies_last_hour": recent_anomalies[0]["n"] if recent_anomalies else 0,
        "critical_anomalies_last_hour": recent_critical[0]["n"] if recent_critical else 0,
        "alerts_last_hour": recent_alerts[0]["n"] if recent_alerts else 0,
    }


@router.get("/streaming-status")
def streaming_status() -> dict[str, Any]:
    return _services_health()


# ---------------------------------------------------------------------------
# Diff-poll helpers (mirrors api/realtime_api.py)
# ---------------------------------------------------------------------------


async def _fetch_topic(table: str, ts_column: str, limit: int, since: str | None) -> list[dict[str, Any]]:
    if since is None:
        sql = f"select * from {table} order by {ts_column} desc limit {limit}"
        params = None
    else:
        sql = f"select * from {table} where {ts_column} > ? order by {ts_column} desc limit {limit}"
        params = [since]
    return await asyncio.to_thread(query_safe, sql, params)


async def _poll_delta(cursors: dict[str, str | None]) -> dict[str, Any]:
    changed: dict[str, list[dict[str, Any]]] = {}
    for topic, (table, ts_column, limit) in _TOPICS.items():
        rows = await _fetch_topic(table, ts_column, limit, cursors[topic])
        if rows:
            changed[topic] = rows
            newest = rows[0].get(ts_column)
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
async def monitoring_ws(websocket: WebSocket) -> None:
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
# B. SSE endpoint
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
async def monitoring_stream() -> StreamingResponse:
    return StreamingResponse(
        _sse_event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
