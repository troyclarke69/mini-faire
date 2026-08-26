"""Autonomy API (PHASE9-AUTONOMY.md Section 8).

Mounted into `api/metrics_api.py` alongside `ml_router`/`monitoring_router`/
`simulation_router`/etc. Thin FastAPI wrapper (same posture `api/simulation_
api.py`'s docstring already states) around `orchestration/agent_flow.py` and
the five `autonomy.*` tables `autonomy/agent_framework.persist_actions()`
writes to - no agent decision logic of its own lives here, only request
validation, error-code mapping, and response shaping.

Deliberately left open (no `require_tenant()` gate), matching `api/ml_api.
py`/`api/monitoring_api.py`/`api/simulation_api.py` - a caller can still
scope a request to one tenant via the optional `tenant_id` query/body field,
threading straight through to `agent_flow.run_agent_flow(tenant_id=...)`.

Section 8's endpoints map onto `orchestration/agent_flow.py`/`autonomy/*.py`
as:

- `POST /autonomy/run` -> `agent_flow.run_agent_flow()` - the full Section 7
  orchestrated pass (all five agents decide, conflicts resolve, actions
  apply and persist). Body selects `mode` ("live"/"tick"/"scenario") and
  every other `run_agent_flow()` keyword.
- `GET /autonomy/actions` -> every `autonomy.*_actions` table merged and
  sorted by `created_at` (newest first) - the combined decision feed;
  `GET /autonomy/pricing`, `/inventory`, `/demand`, `/anomalies`,
  `/retailer-strategy` are the same read scoped to one agent's own table
  (Section 8's five per-agent-type endpoints).
- `GET /autonomy/conflicts` -> `autonomy.conflicts` (added in `agent_flow.py`
  specifically so this endpoint - and the WS/SSE conflicts topic below -
  has a real persisted source rather than re-deriving "what lost to what"
  from `status='rejected'` rows at request time).
- `GET /autonomy/performance` -> per-agent-type action counts/status
  breakdown/average reward, read straight off the same five tables'
  `status`/`reward` columns - `AgentPerformanceChart`'s data source.
- `GET /autonomy/state` -> NOT live in-memory agent state (agent instances
  are ephemeral, built fresh once per `run_agent_flow()` call - see
  `agent_framework.BaseAutonomousAgent`'s docstring, same "no persisted
  agent object" posture `simulation/scenario_engine.py`'s agents already
  have); this returns the twin summary, the last recorded run per
  agent_type (`elt_model_runs`, `load_strategy='autonomy_agent'`), the
  current pipeline-health reading, and each agent type's default
  `AgentConstraints` - what `AgentStateVisualizer` needs to render.

WebSocket/SSE push (Section 8: WS/SSE for new decisions/conflicts/
resolutions/performance) is the same diff-poll pattern `api/simulation_api.
py`/`api/ml_api.py`/`api/monitoring_api.py` already use (see `api/ml_api.
py`'s docstring for the full rationale): six topics (one per action table
plus conflicts) watched by `created_at`, with a freshly-recomputed
`performance` snapshot attached to every non-empty update - "resolutions"
is not a separate topic, since every `autonomy.conflicts` row already IS a
resolution record (it names both the winning and the rejected action, see
`agent_flow._resolve_and_apply()`'s docstring), so a dedicated `conflicts`
topic covers both bullets without inventing a second, redundant one.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.db import query_safe
from autonomy.agent_framework import AgentConstraints
from ingestion.paths import DUCKDB_PATH
from orchestration.agent_flow import AGENT_TYPE_PRIORITY, RUN_MODES, run_agent_flow
from simulation.digital_twin import load_digital_twin

router = APIRouter(prefix="/autonomy", tags=["autonomy"])

# agent_type -> its own autonomy.<table> - the same table_name each agent
# class in autonomy/*.py carries as a class attribute, duplicated here as a
# plain dict since this module reads via api/db.py's query_safe() rather
# than importing every agent class just to read one attribute off each.
_ACTION_TABLES: dict[str, str] = {
    "pricing": "pricing_actions",
    "inventory": "inventory_actions",
    "demand": "demand_actions",
    "anomaly_response": "anomaly_actions",
    "retailer_strategy": "retailer_strategy_actions",
}


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class AgentRunRequest(BaseModel):
    mode: str = "live"
    tenant_id: str | None = None
    scenario_type: str | None = None
    scenario_params: dict[str, Any] | None = None
    rounds: int = 1
    ticks_per_round: int = 1
    seed: int = 42


# ---------------------------------------------------------------------------
# Shared read helpers
# ---------------------------------------------------------------------------


def _read_actions(table_name: str, *, tenant_id: str | None, limit: int) -> list[dict[str, Any]]:
    """Reads `autonomy.<table_name>` via `api/db.py`'s `query_safe()`
    (missing-table degrades to `[]`, same convention every other router in
    this repo already uses) rather than `agent_framework.list_actions()`,
    which opens its own separate `connect_with_retry()` connection -
    redundant with `api/db.py`'s existing one, same reasoning `api/
    simulation_api.py`'s docstring already gives for preferring `query_safe()`
    over `scenario_engine.list_scenario_results()` there."""
    if tenant_id is None:
        return query_safe(f"select * from autonomy.{table_name} order by created_at desc limit ?", [limit])
    return query_safe(
        f"select * from autonomy.{table_name} where tenant_id = ? order by created_at desc limit ?",
        [tenant_id, limit],
    )


def _read_all_actions(*, tenant_id: str | None, limit: int) -> list[dict[str, Any]]:
    """Merges all five action tables in Python rather than a SQL `UNION
    ALL` - every table shares the identical column shape (`agent_framework.
    _ACTION_COLUMNS`), so a union would work, but it would fail closed for
    every table the instant even ONE of the five doesn't exist yet (e.g.
    early in a demo, before every agent type has ever run) - `query_safe()`
    degrading per-table to `[]` is a materially better failure mode here."""
    merged: list[dict[str, Any]] = []
    for agent_type, table_name in _ACTION_TABLES.items():
        rows = _read_actions(table_name, tenant_id=tenant_id, limit=limit)
        for row in rows:
            row.setdefault("agent_type", agent_type)
        merged.extend(rows)
    merged.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return merged[:limit]


def _read_conflicts(*, limit: int) -> list[dict[str, Any]]:
    return query_safe("select * from autonomy.conflicts order by created_at desc limit ?", [limit])


def _compute_performance() -> list[dict[str, Any]]:
    """Per-agent-type action counts/status breakdown/average reward, read
    straight off each agent's own table - `AgentPerformanceChart`'s data
    source, and what the WS/SSE `performance` snapshot attaches to every
    non-empty update below."""
    out: list[dict[str, Any]] = []
    for agent_type, table_name in _ACTION_TABLES.items():
        rows = query_safe(
            f"""
            select
              count(*) as action_count,
              sum(case when status = 'applied' then 1 else 0 end) as applied_count,
              sum(case when status = 'rejected' then 1 else 0 end) as rejected_count,
              sum(case when status = 'proposed' then 1 else 0 end) as advisory_count,
              avg(reward) as average_reward
            from autonomy.{table_name}
            """
        )
        row = rows[0] if rows else {}
        out.append({
            "agent_type": agent_type,
            "action_count": row.get("action_count") or 0,
            "applied_count": row.get("applied_count") or 0,
            "rejected_count": row.get("rejected_count") or 0,
            "advisory_count": row.get("advisory_count") or 0,
            "average_reward": row.get("average_reward"),
        })
    return out


def _last_run_by_agent_type() -> dict[str, dict[str, Any]]:
    """Most recent `elt_model_runs` row per agent_type (`load_strategy=
    'autonomy_agent'`, `model_name=<agent_type>` - see `agent_flow.
    _append_agent_run()`) - what `/autonomy/state` reports as "when did
    each agent last run, and did it succeed"."""
    rows = query_safe(
        "select model_name, target_table, started_at, completed_at, status from elt_model_runs "
        "where load_strategy = 'autonomy_agent' order by completed_at desc limit 200"
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        agent_type = row.get("model_name")
        if agent_type and agent_type not in out:
            out[agent_type] = row
    return out


def _pipeline_healthy_reading(*, window_hours: float = 1.0) -> bool:
    """Same real, best-effort-degrades-to-True signal `agent_flow.
    _pipeline_health_check()` computes for `AgentContext.pipeline_healthy` -
    reimplemented locally against `query_safe()` rather than importing that
    leading-underscore helper across the `api` <-> `orchestration` package
    boundary (this repo reserves cross-package private imports for genuine
    sibling-module cases - see `simulation/counterfactuals.py`'s module
    docstring - a materially different situation from this API module
    wanting the same one-line reading)."""
    rows = query_safe(
        f"select count(*) as c from monitoring.alert_events "
        f"where severity = 'critical' and created_at > current_timestamp - interval '{window_hours} hours'"
    )
    return not rows or not rows[0].get("c")


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.post("/run")
async def autonomy_run(body: AgentRunRequest | None = None) -> dict[str, Any]:
    body = body or AgentRunRequest()
    if body.mode not in RUN_MODES:
        raise HTTPException(status_code=400, detail=f"unknown mode {body.mode!r} - must be one of {RUN_MODES}")
    if body.mode == "scenario" and not body.scenario_type:
        raise HTTPException(status_code=400, detail="mode='scenario' requires scenario_type")
    try:
        return await asyncio.to_thread(
            run_agent_flow,
            mode=body.mode,
            tenant_id=body.tenant_id,
            scenario_type=body.scenario_type,
            scenario_params=body.scenario_params,
            rounds=body.rounds,
            ticks_per_round=body.ticks_per_round,
            seed=body.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/actions")
def autonomy_actions(tenant_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    return {"actions": _read_all_actions(tenant_id=tenant_id, limit=limit)}


@router.get("/pricing")
def autonomy_pricing(tenant_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    return {"actions": _read_actions(_ACTION_TABLES["pricing"], tenant_id=tenant_id, limit=limit)}


@router.get("/inventory")
def autonomy_inventory(tenant_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    return {"actions": _read_actions(_ACTION_TABLES["inventory"], tenant_id=tenant_id, limit=limit)}


@router.get("/demand")
def autonomy_demand(tenant_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    return {"actions": _read_actions(_ACTION_TABLES["demand"], tenant_id=tenant_id, limit=limit)}


@router.get("/anomalies")
def autonomy_anomalies(tenant_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    return {"actions": _read_actions(_ACTION_TABLES["anomaly_response"], tenant_id=tenant_id, limit=limit)}


@router.get("/retailer-strategy")
def autonomy_retailer_strategy(tenant_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    return {"actions": _read_actions(_ACTION_TABLES["retailer_strategy"], tenant_id=tenant_id, limit=limit)}


@router.get("/conflicts")
def autonomy_conflicts(limit: int = 50) -> dict[str, Any]:
    return {"conflicts": _read_conflicts(limit=limit)}


@router.get("/performance")
def autonomy_performance() -> dict[str, Any]:
    return {"performance": _compute_performance()}


@router.get("/state")
def autonomy_state(tenant_id: str | None = None) -> dict[str, Any]:
    if not DUCKDB_PATH.exists():
        raise HTTPException(status_code=404, detail="Warehouse not built. Run scripts/run_demo.py first.")
    twin = load_digital_twin(tenant_id, DUCKDB_PATH)
    return {
        "twin_summary": twin.to_summary_dict(),
        "pipeline_healthy": _pipeline_healthy_reading(),
        "agent_type_priority": list(AGENT_TYPE_PRIORITY),
        "default_constraints": asdict(AgentConstraints()),
        "last_run_by_agent_type": _last_run_by_agent_type(),
    }


# ---------------------------------------------------------------------------
# Diff-poll helpers (mirrors api/simulation_api.py)
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 20.0

# topic -> (source table, ORDER/WHERE timestamp column, row limit per poll)
_TOPICS: dict[str, tuple[str, str, int]] = {
    "pricing": ("autonomy.pricing_actions", "created_at", 50),
    "inventory": ("autonomy.inventory_actions", "created_at", 50),
    "demand": ("autonomy.demand_actions", "created_at", 50),
    "anomalies": ("autonomy.anomaly_actions", "created_at", 50),
    "retailer_strategy": ("autonomy.retailer_strategy_actions", "created_at", 50),
    "conflicts": ("autonomy.conflicts", "created_at", 50),
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
    changed: dict[str, list[dict[str, Any]]] = {}
    for topic, (table, ts_column, limit) in _TOPICS.items():
        rows = await _fetch_topic(table, ts_column, limit, cursors[topic])
        if rows:
            changed[topic] = rows
            newest = rows[0].get(ts_column)
            if newest:
                cursors[topic] = newest
    if changed:
        # A fresh performance snapshot rides along with every non-empty
        # update - see module docstring's "resolutions is not a separate
        # topic" note for why this isn't itself a diffed topic.
        changed["performance"] = await asyncio.to_thread(_compute_performance)
    return changed


def _new_cursors() -> dict[str, str | None]:
    return {topic: None for topic in _TOPICS}


def _envelope(changed: dict[str, Any], *, kind: str) -> dict[str, Any]:
    return {"type": kind, "server_time": datetime.now(UTC).isoformat(), **changed}


# ---------------------------------------------------------------------------
# A. WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def autonomy_ws(websocket: WebSocket) -> None:
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
async def autonomy_stream() -> StreamingResponse:
    return StreamingResponse(
        _sse_event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
