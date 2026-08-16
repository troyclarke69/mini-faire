from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import duckdb
from fastapi import FastAPI, HTTPException

from ingestion.paths import DUCKDB_PATH


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
