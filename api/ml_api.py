"""ML API (PHASE6-ML.md Section 8).

REST endpoints expose the five ML warehouse tables Phase 6 writes
(ml.forecasts, ml.clusters, ml.recommendations, ml.anomaly_classifications,
ml.model_registry) plus ml.features, all through api/db.py's query_safe() -
identical convention to api/metrics_api.py and api/monitoring_api.py: a
missing table (fresh checkout, before orchestration/ml_training_flow.py has
ever run) degrades to an empty list rather than a 500, so the frontend's ML
dashboards render an empty/"not trained yet" state instead of erroring.

The WebSocket/SSE push endpoints below are the same diff-poll pattern
api/monitoring_api.py and api/realtime_api.py already use (see
api/monitoring_api.py's docstring for the full rationale), applied to the
four topics PHASE6-ML.md Section 8 asks for push updates on: new forecasts,
clusters, recommendations, and anomaly classifications. Duplicated rather
than shared for the same reason api/monitoring_api.py's copy is duplicated
rather than generalized - the topic tables and timestamp columns differ per
router, and a shared abstraction would need nearly as many parameters as it
saved lines. ml.model_registry and ml.features are deliberately NOT push
topics: registry changes only happen once per training run (infrequent, and
already visible via a REST refetch), and ml.features is an internal input
to the model layer rather than something a dashboard live-tails.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from api.db import query_safe

router = APIRouter(prefix="/ml", tags=["ml"])

POLL_INTERVAL_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 20.0

# topic -> (source table, ORDER/WHERE timestamp column, row limit per poll)
_TOPICS: dict[str, tuple[str, str, int]] = {
    "forecasts": ("ml.forecasts", "generated_at", 100),
    "clusters": ("ml.clusters", "computed_at", 100),
    "recommendations": ("ml.recommendations", "generated_at", 100),
    "anomaly_classifications": ("ml.anomaly_classifications", "classified_at", 50),
}


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.get("/forecasts")
def forecasts() -> list[dict]:
    return query_safe("select * from ml.forecasts order by generated_at desc, forecast_type, entity_id, target_date limit 2000")


@router.get("/clusters")
def clusters() -> list[dict]:
    return query_safe("select * from ml.clusters order by computed_at desc, entity_type, entity_id limit 1000")


@router.get("/recommendations")
def recommendations() -> list[dict]:
    return query_safe("select * from ml.recommendations order by generated_at desc, recommendation_type, source_entity_id, rank limit 1000")


@router.get("/anomalies/classified")
def anomalies_classified() -> list[dict]:
    return query_safe("select * from ml.anomaly_classifications order by classified_at desc limit 500")


@router.get("/models")
def models() -> list[dict]:
    # Every registered version, not just the active one - the frontend's
    # ModelRegistryTable is meant to show the full train/promote/rollback
    # history per model_name (see ml/registry.py's status vocabulary), not
    # just what's live right now.
    return query_safe("select * from ml.model_registry order by model_name, version desc")


@router.get("/features")
def features() -> list[dict]:
    return query_safe("select * from ml.features order by computed_at desc limit 1000")


# ---------------------------------------------------------------------------
# Diff-poll helpers (mirrors api/monitoring_api.py / api/realtime_api.py)
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
async def ml_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    cursors = _new_cursors()

    initial = await _poll_delta(cursors)
    await websocket.send_json(_envelope(initial, kind="snapshot"))

    last_sent_at = asyncio.get_event_loop().time()
    try:
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
async def ml_stream() -> StreamingResponse:
    return StreamingResponse(
        _sse_event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
