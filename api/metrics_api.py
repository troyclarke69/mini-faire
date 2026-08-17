from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException

from ingestion.paths import DUCKDB_PATH, RAW_DIR


app = FastAPI(title="Mini Faire Metrics API", version="0.1.0")


def to_json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def query(sql: str) -> list[dict]:
    if not DUCKDB_PATH.exists():
        raise HTTPException(status_code=404, detail="Warehouse not built. Run scripts/run_demo.py first.")
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        result = con.execute(sql)
        columns = [column[0] for column in result.description]
        return [
            {column: to_json_value(value) for column, value in zip(columns, row)}
            for row in result.fetchall()
        ]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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


def run_id_from_quarantine_path(path: Path) -> str:
    try:
        return path.parents[1].name
    except IndexError:
        return "unknown"


def entity_from_quarantine_path(path: Path) -> str:
    parts = path.parts
    if "batch" in parts:
        return parts[parts.index("batch") + 1]
    if "events" in parts:
        return parts[parts.index("events") + 1]
    return "unknown"
