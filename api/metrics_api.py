from __future__ import annotations

import json

import duckdb
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from api.autonomy_api import router as autonomy_router
from api.db import query
from api.ml_api import router as ml_router
from api.monitoring_api import router as monitoring_router
from api.realtime_api import router as realtime_router
from api.simulation_api import router as simulation_router
from api.tenant_api import router as tenant_router
from auth.auth_api import router as auth_router
from auth.auth_middleware import RateLimitMiddleware
from ingestion.paths import RAW_DIR
from ingestion.quarantine import entity_from_quarantine_path, run_id_from_quarantine_path
from observability.logging import configure_json_logging
from observability.metrics import make_api_metrics_middleware, refresh_from_warehouse, render_metrics_response
from observability.tracing import init_tracing, make_tracing_middleware

app = FastAPI(title="Mini Faire Metrics API", version="0.1.0")

# The Next.js frontend's Server Components fetch this API from the Node
# server (same-origin as far as the browser is concerned), so no CORS was
# needed before. Phase 4's Live Mode connects directly from the *browser* to
# this API's WebSocket/SSE endpoints (frontend/lib/realtime.ts), which is a
# genuine cross-origin request (localhost:3000 -> localhost:8000) - hence
# CORS middleware, scoped to localhost dev origins rather than left wide open.
# Phase 7 (PHASE7-DEPLOYMENT.md Section 3/4): auth/auth_api.py's
# signup/login/refresh/logout are POST, and frontend/lib/auth.ts calls them
# directly from the browser the same way Live Mode calls the WS/SSE
# endpoints - POST joins GET in allow_methods for that reason. Bearer tokens
# travel in the Authorization header, not cookies, so allow_credentials
# stays False (no cross-origin cookie use to permit).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
# Phase 7 (PHASE7-DEPLOYMENT.md Section 3): applies to every route below,
# not just auth/tenant-scoped ones - config/auth.yaml's rate_limit block
# (enabled by default) is the one place that's tuned, not per-endpoint.
app.add_middleware(RateLimitMiddleware)
# Phase 7 (PHASE7-DEPLOYMENT.md Section 8): observability/metrics.py's
# api_request_duration_seconds/api_errors_total and observability/tracing.py's
# per-request span, added last (Starlette runs middleware in reverse
# registration order, outermost-added-last-runs-first-on-the-way-in) so a
# request is measured/traced around the auth and rate-limit middleware too,
# not just the route handler - a 429 from RateLimitMiddleware or a 401 from
# require_role() is exactly the kind of response Grafana/Jaeger need to see.
app.add_middleware(make_tracing_middleware())
app.add_middleware(make_api_metrics_middleware())

app.include_router(realtime_router)
app.include_router(monitoring_router)
app.include_router(ml_router)
app.include_router(auth_router)
app.include_router(tenant_router)
app.include_router(simulation_router)
app.include_router(autonomy_router)


# ingestion/duckdb_utils.py's connect_with_retry() already absorbs a *brief*
# lock collision (retrying with backoff instead of raising immediately - see
# that module's docstring), but DuckDB is still single-writer: if another
# process (orchestration/realtime_flow.py mid-cycle, synthetic/
# stream_generator.py, a stray leftover process from a previous run) holds a
# write lock for longer than that retry budget, connect_with_retry() still
# raises duckdb.IOException in the end, and every write path in this repo
# (auth/auth_api.py's signup/login/refresh/logout in particular - the one
# most likely to be hit by an interactive user right as a background
# ingest/ELT/compute cycle is running) lets that propagate rather than
# catching it, which without this handler surfaces to the client as a raw
# 500 + Python traceback instead of a clear "try again" signal. This handler
# is app-wide (every router, not just auth/tenant) since any of them can hit
# the same lock, mirroring how RateLimitMiddleware/CORS above are also
# applied once at the app level rather than per-endpoint.
@app.exception_handler(duckdb.IOException)
def _duckdb_locked_handler(request: Request, exc: duckdb.IOException) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "The database is temporarily busy - another process (an ingest run, ELT "
                "rebuild, or compute pass) is writing to it right now. Please retry in a "
                "few seconds."
            )
        },
        headers={"Retry-After": "3"},
    )


@app.on_event("startup")
def _configure_observability() -> None:
    # Idempotent (see configure_json_logging()/init_tracing()'s own
    # docstrings) - safe even if this app object is imported more than once
    # (e.g. by a test) or FastAPI's startup handler runs more than once.
    configure_json_logging("backend")
    init_tracing("backend")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/observability/metrics", response_class=PlainTextResponse)
def observability_metrics() -> PlainTextResponse:
    # Re-reads the warehouse's already-collected signals on every scrape
    # (see observability/metrics.py's refresh_from_warehouse() docstring)
    # rather than only reflecting in-process counters - so ingestion/ELT/
    # compute/ML durations show up even though those flows run as separate
    # processes (orchestration/*.py), not inside this API process.
    refresh_from_warehouse()
    body, content_type = render_metrics_response()
    return PlainTextResponse(content=body, media_type=content_type)


@app.get("/metrics/retailer-daily")
def retailer_daily() -> list[dict]:
    return query("select * from marts.metrics_retailer_daily order by order_date, retailer_id")


@app.get("/metrics/product-velocity")
def product_velocity() -> list[dict]:
    return query("select * from marts.metrics_product_velocity order by inventory_velocity desc nulls last")


@app.get("/metrics/order-profitability")
def order_profitability() -> list[dict]:
    return query("select * from marts.metrics_order_profitability order by order_date, order_id")


@app.get("/compute/retailer-health")
def retailer_health() -> list[dict]:
    return query("select * from marts.compute_retailer_health order by retailer_health_score desc")


@app.get("/compute/product-reorder-risk")
def product_reorder_risk() -> list[dict]:
    return query("select * from marts.compute_product_reorder_risk order by reorder_risk_score desc")


@app.get("/compute/brand-contribution")
def brand_contribution() -> list[dict]:
    return query("select * from marts.compute_brand_contribution order by gmv desc")


@app.get("/compute/retailer-cohort-retention")
def retailer_cohort_retention() -> list[dict]:
    return query(
        """
        select *
        from marts.compute_retailer_cohort_retention
        order by signup_month, cohort_age_months
        """
    )


@app.get("/compute/event-lag-summary")
def event_lag_summary() -> list[dict]:
    return query("select * from marts.compute_event_lag_summary order by event_type")


@app.get("/compute/inventory-movement")
def inventory_movement() -> list[dict]:
    return query("select * from marts.compute_inventory_movement order by total_delta")


@app.get("/compute/order-lifecycle")
def order_lifecycle() -> list[dict]:
    return query("select * from marts.compute_order_lifecycle")


@app.get("/compute/model-runs")
def compute_model_runs() -> list[dict]:
    return query("select * from marts.compute_model_runs order by computed_at desc")


@app.get("/metadata/ingestion-runs")
def ingestion_runs() -> list[dict]:
    return query("select * from ingestion_runs order by completed_at desc")


@app.get("/metadata/lineage-edges")
def lineage_edges() -> list[dict]:
    return query("select * from lineage_edges order by created_at desc, run_id")


@app.get("/metadata/elt-model-runs")
def elt_model_runs() -> list[dict]:
    return query("select * from elt_model_runs order by completed_at desc")


@app.get("/metadata/quarantine-records")
def quarantine_records() -> list[dict]:
    records: list[dict] = []
    for path in RAW_DIR.glob("**/quarantine/*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload:
            records.append(
                {
                    "path": str(path),
                    "run_id": run_id_from_quarantine_path(path),
                    "entity": entity_from_quarantine_path(path),
                    "record_index": item.get("record_index"),
                    "record": item.get("record"),
                    "errors": item.get("errors", []),
                }
            )
    return records
