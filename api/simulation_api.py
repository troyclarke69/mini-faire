"""Simulation API (PHASE8-SIMULATION.md Section 6).

Mounted into `api/metrics_api.py` alongside `ml_router`/`monitoring_router`/
`realtime_router`. Thin FastAPI wrapper (same posture `auth/auth_api.py`'s
docstring already states for that router) around the four modules the rest
of Phase 8 already built - `simulation/digital_twin.py`, `simulation/
scenario_engine.py`, `simulation/counterfactuals.py`, and `orchestration/
simulation_flow.py` - no simulation logic of its own lives here, only
request validation, error-code mapping, and response shaping.

Deliberately left open (no `require_tenant()` gate), matching `api/ml_api.
py`/`api/monitoring_api.py`/`api/realtime_api.py` - `api/tenant_api.py`'s
docstring already notes those three stay open by design, and Phase 8 didn't
ask for a tenant-gated simulation surface. A caller can still scope a
request to one tenant's twin via the optional `tenant_id` query/body field,
which threads straight through to `simulation.digital_twin.
load_digital_twin(tenant_id, ...)` the same way every other simulation
module already supports.

Section 6's six endpoints map onto the underlying modules as:

- `POST /simulation/run` -> `orchestration.simulation_flow.
  run_simulation_flow()` - the full Section 5 orchestrated batch (twin load,
  agent load, baseline projection, a scenario batch, a counterfactual
  batch). An empty/omitted body runs `run_simulation_flow()`'s own
  data-derived defaults (see that function's docstring) - the same "just
  run it" entrypoint Section 8 asks `python orchestration/simulation_flow.py`
  to provide, exposed over HTTP.
- `GET /simulation/scenarios` -> `scenario_engine.SCENARIO_TYPES`/
  `SCENARIO_PARAM_SCHEMA`, the catalog `ScenarioBuilder` renders a form
  from. `POST /simulation/scenarios` runs exactly one ad-hoc scenario via
  `scenario_engine.run_scenario()` - the interactive counterpart to the
  batch `/simulation/run` triggers, and the only endpoint that accepts
  `retailer_strategy_overrides`/`product_strategy_overrides` (constructed
  here from the request body's plain dicts into real `RetailerStrategy`/
  `ProductStrategy` dataclass instances - see `_build_strategy_overrides()`
  - `scenario_engine.run_scenario()` itself keeps typed dataclass params,
  not raw JSON).
- `GET`/`POST /simulation/counterfactuals` -> the same catalog/run-one split
  against `counterfactuals.COUNTERFACTUAL_TYPES`/`run_counterfactual()`.
- `GET /simulation/state` -> `digital_twin.load_digital_twin()`'s full
  snapshot (summary + every retailer/product/anomaly record) - what
  `DigitalTwinVisualizer` renders.
- `GET /simulation/agents` -> NOT a persisted resource (`build_agents()`
  builds fresh, ephemeral agent objects every run - see that function's
  docstring) - this returns the *default* `MarketplaceStrategy`/
  `RetailerStrategy`/`ProductStrategy` field values plus the twin's current
  retailer/product id set, which is what `AgentStrategyEditor` needs to
  build a per-entity override form; edits round-trip back through
  `POST /simulation/scenarios`'s override fields above, not through this
  endpoint.
- `GET /simulation/results` -> both `simulation.scenario_results` and
  `simulation.counterfactual_results`, most-recent-first, read via
  `api/db.py`'s `query_safe()` (same "missing table degrades to []"
  convention every other router already uses) rather than through
  `scenario_engine.list_scenario_results()`/`counterfactuals.
  list_counterfactual_results()` - both of those open their own
  `connect_with_retry()` connection, which is redundant with `api/db.py`'s
  existing one. `GET /simulation/results/scenario/{scenario_id}` and
  `/results/counterfactual/{counterfactual_id}` fetch one row's full detail
  for `SimulationResultCharts`.

WebSocket/SSE push (Section 6: "simulation progress, simulation results,
scenario outcomes, counterfactual outcomes") is the same diff-poll pattern
`api/ml_api.py`/`api/monitoring_api.py`/`api/realtime_api.py` already use
(see `api/ml_api.py`'s docstring for the full rationale) watching
`simulation.scenario_results`/`simulation.counterfactual_results` for new
rows by `completed_at`. There is no separate intra-run "percent complete"
progress table - each scenario/counterfactual run is short enough (seconds,
not minutes) that a completed row arriving *is* the progress signal at the
granularity this phase needs: watching this stream during a `/simulation/
run` batch shows each scenario/counterfactual complete one at a time, which
is what "simulation progress" cashes out to here rather than a fabricated
percentage. Documented simplification, not silently claimed as finer-grained
than it is.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.db import query_safe
from ingestion.paths import DUCKDB_PATH
from orchestration.simulation_flow import run_simulation_flow
from simulation import counterfactuals, scenario_engine
from simulation.digital_twin import load_digital_twin

router = APIRouter(prefix="/simulation", tags=["simulation"])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class SimulationFlowRequest(BaseModel):
    tenant_id: str | None = None
    scenario_specs: list[dict[str, Any]] | None = None
    counterfactual_specs: list[dict[str, Any]] | None = None
    projection_ticks: int = scenario_engine.DEFAULT_TICKS
    seed: int = 42


class ScenarioRunRequest(BaseModel):
    scenario_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str | None = None
    ticks: int = scenario_engine.DEFAULT_TICKS
    seed: int = 42
    retailer_strategy_overrides: dict[str, dict[str, Any]] | None = None
    product_strategy_overrides: dict[str, dict[str, Any]] | None = None


class CounterfactualRunRequest(BaseModel):
    counterfactual_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    start_date: str | None = None
    end_date: str | None = None
    replay_ticks: int = counterfactuals.DEFAULT_REPLAY_TICKS
    seed: int = 42


def _build_strategy_overrides(raw: dict[str, dict[str, Any]] | None, cls: type) -> dict[str, Any] | None:
    """Turns a request body's plain `{entity_id: {field: value}}` dict into
    `{entity_id: cls(**fields)}` - a wrong field name fails fast as a 400
    (via `TypeError` from the dataclass constructor) rather than being
    silently ignored the way a loose passthrough dict would."""
    if not raw:
        return None
    try:
        return {entity_id: cls(**fields) for entity_id, fields in raw.items()}
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid strategy override: {exc}") from exc


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.post("/run")
async def simulation_run(body: SimulationFlowRequest | None = None) -> dict[str, Any]:
    body = body or SimulationFlowRequest()
    return await asyncio.to_thread(
        run_simulation_flow,
        tenant_id=body.tenant_id,
        scenario_specs=body.scenario_specs,
        counterfactual_specs=body.counterfactual_specs,
        projection_ticks=body.projection_ticks,
        seed=body.seed,
    )


@router.get("/scenarios")
def scenario_catalog() -> dict[str, Any]:
    return {"scenario_types": list(scenario_engine.SCENARIO_TYPES), "param_schema": scenario_engine.SCENARIO_PARAM_SCHEMA}


@router.post("/scenarios")
async def scenario_run(body: ScenarioRunRequest) -> dict[str, Any]:
    if body.scenario_type not in scenario_engine.SCENARIO_TYPES:
        raise HTTPException(status_code=400, detail=f"unknown scenario_type {body.scenario_type!r} - must be one of {scenario_engine.SCENARIO_TYPES}")
    retailer_overrides = _build_strategy_overrides(body.retailer_strategy_overrides, scenario_engine.RetailerStrategy)
    product_overrides = _build_strategy_overrides(body.product_strategy_overrides, scenario_engine.ProductStrategy)
    try:
        result = await asyncio.to_thread(
            scenario_engine.run_scenario,
            body.scenario_type,
            body.params,
            tenant_id=body.tenant_id,
            ticks=body.ticks,
            seed=body.seed,
            retailer_strategy_overrides=retailer_overrides,
            product_strategy_overrides=product_overrides,
            persist=True,
        )
    except scenario_engine.ScenarioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.__dict__


@router.get("/counterfactuals")
def counterfactual_catalog() -> dict[str, Any]:
    return {
        "counterfactual_types": list(counterfactuals.COUNTERFACTUAL_TYPES),
        "param_schema": counterfactuals.COUNTERFACTUAL_PARAM_SCHEMA,
    }


@router.post("/counterfactuals")
async def counterfactual_run(body: CounterfactualRunRequest) -> dict[str, Any]:
    if body.counterfactual_type not in counterfactuals.COUNTERFACTUAL_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown counterfactual_type {body.counterfactual_type!r} - must be one of {counterfactuals.COUNTERFACTUAL_TYPES}",
        )
    try:
        result = await asyncio.to_thread(
            counterfactuals.run_counterfactual,
            body.counterfactual_type,
            body.params,
            start_date=body.start_date,
            end_date=body.end_date,
            replay_ticks=body.replay_ticks,
            seed=body.seed,
            persist=True,
        )
    except counterfactuals.CounterfactualError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.__dict__


@router.get("/state")
def simulation_state(tenant_id: str | None = None) -> dict[str, Any]:
    if not DUCKDB_PATH.exists():
        raise HTTPException(status_code=404, detail="Warehouse not built. Run scripts/run_demo.py first.")
    twin = load_digital_twin(tenant_id, DUCKDB_PATH)
    return {
        "summary": twin.to_summary_dict(),
        "retailers": [asdict(r) for r in twin.retailers.values()],
        "products": [asdict(p) for p in twin.products.values()],
        "recent_anomalies": [asdict(a) for a in twin.recent_anomalies],
    }


@router.get("/agents")
def agents_catalog(tenant_id: str | None = None) -> dict[str, Any]:
    if not DUCKDB_PATH.exists():
        raise HTTPException(status_code=404, detail="Warehouse not built. Run scripts/run_demo.py first.")
    twin = load_digital_twin(tenant_id, DUCKDB_PATH)
    return {
        "retailer_ids": list(twin.retailers.keys()),
        "product_ids": list(twin.products.keys()),
        "default_marketplace_strategy": asdict(scenario_engine.MarketplaceStrategy()),
        "default_retailer_strategy": asdict(scenario_engine.RetailerStrategy()),
        "default_product_strategy": asdict(scenario_engine.ProductStrategy()),
    }


@router.get("/results")
def simulation_results(tenant_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    if tenant_id is None:
        scenarios = query_safe("select * from simulation.scenario_results order by completed_at desc limit ?", [limit])
    else:
        scenarios = query_safe(
            "select * from simulation.scenario_results where tenant_id = ? order by completed_at desc limit ?",
            [tenant_id, limit],
        )
    counterfactual_rows = query_safe(
        "select * from simulation.counterfactual_results order by completed_at desc limit ?", [limit]
    )
    return {"scenarios": scenarios, "counterfactuals": counterfactual_rows}


@router.get("/results/scenario/{scenario_id}")
def scenario_result_detail(scenario_id: str) -> dict[str, Any]:
    rows = query_safe("select * from simulation.scenario_results where scenario_id = ?", [scenario_id])
    if not rows:
        raise HTTPException(status_code=404, detail="scenario result not found")
    return rows[0]


@router.get("/results/counterfactual/{counterfactual_id}")
def counterfactual_result_detail(counterfactual_id: str) -> dict[str, Any]:
    rows = query_safe(
        "select * from simulation.counterfactual_results where counterfactual_id = ?", [counterfactual_id]
    )
    if not rows:
        raise HTTPException(status_code=404, detail="counterfactual result not found")
    return rows[0]


# ---------------------------------------------------------------------------
# Diff-poll helpers (mirrors api/ml_api.py / api/monitoring_api.py)
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 20.0

# topic -> (source table, ORDER/WHERE timestamp column, row limit per poll)
_TOPICS: dict[str, tuple[str, str, int]] = {
    "scenario_results": ("simulation.scenario_results", "completed_at", 50),
    "counterfactual_results": ("simulation.counterfactual_results", "completed_at", 50),
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
    return changed


def _new_cursors() -> dict[str, str | None]:
    return {topic: None for topic in _TOPICS}


def _envelope(changed: dict[str, Any], *, kind: str) -> dict[str, Any]:
    return {"type": kind, "server_time": datetime.now(UTC).isoformat(), **changed}


# ---------------------------------------------------------------------------
# A. WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def simulation_ws(websocket: WebSocket) -> None:
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
async def simulation_stream() -> StreamingResponse:
    return StreamingResponse(
        _sse_event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
