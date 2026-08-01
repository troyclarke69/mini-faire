from __future__ import annotations

import duckdb
from fastapi import FastAPI, HTTPException

from ingestion.paths import DUCKDB_PATH


app = FastAPI(title="Mini Faire Metrics API", version="0.1.0")


def query(sql: str) -> list[dict]:
    if not DUCKDB_PATH.exists():
        raise HTTPException(status_code=404, detail="Warehouse not built. Run scripts/run_demo.py first.")
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        return con.execute(sql).fetchdf().to_dict(orient="records")


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

