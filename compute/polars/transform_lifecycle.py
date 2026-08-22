from __future__ import annotations

import polars as pl

from compute.polars.duckdb_frame import read_duckdb_frame
from ingestion.paths import DUCKDB_PATH


def order_lifecycle_frame(db_path=DUCKDB_PATH) -> pl.DataFrame:
    """Global order_created -> order_paid -> orders_shipped funnel: conversion
    rates and average lag at each stage, derived from marts.fact_orders_events
    now that it carries all three order lifecycle event types."""
    events = read_duckdb_frame(
        """
        select event_type, order_id, cast(event_ts as timestamp) as event_ts
        from marts.fact_orders_events
        where event_type in ('order_created', 'order_paid', 'orders_shipped')
        """,
        db_path,
    )

    created = (
        events.filter(pl.col("event_type") == "order_created")
        .select(pl.col("order_id"), pl.col("event_ts").alias("created_ts"))
        .unique(subset="order_id", keep="first")
    )
    paid = (
        events.filter(pl.col("event_type") == "order_paid")
        .select(pl.col("order_id"), pl.col("event_ts").alias("paid_ts"))
        .unique(subset="order_id", keep="first")
    )
    shipped = (
        events.filter(pl.col("event_type") == "orders_shipped")
        .select(pl.col("order_id"), pl.col("event_ts").alias("shipped_ts"))
        .unique(subset="order_id", keep="first")
    )

    funnel = created.join(paid, on="order_id", how="left").join(shipped, on="order_id", how="left")

    created_count = funnel.height
    paid_count = funnel.filter(pl.col("paid_ts").is_not_null()).height
    shipped_count = funnel.filter(pl.col("shipped_ts").is_not_null()).height

    payment_lag = (
        funnel.filter(pl.col("paid_ts").is_not_null())
        .select(((pl.col("paid_ts") - pl.col("created_ts")).dt.total_seconds()).mean())
        .item()
    )
    shipping_lag = (
        funnel.filter(pl.col("shipped_ts").is_not_null())
        .select(((pl.col("shipped_ts") - pl.col("paid_ts")).dt.total_seconds()).mean())
        .item()
    )

    return pl.DataFrame(
        {
            "created_count": [created_count],
            "paid_count": [paid_count],
            "shipped_count": [shipped_count],
            "paid_rate": [round(paid_count / created_count, 4) if created_count else None],
            "shipped_rate": [round(shipped_count / paid_count, 4) if paid_count else None],
            "avg_payment_lag_seconds": [round(payment_lag, 1) if payment_lag is not None else None],
            "avg_shipping_lag_seconds": [
                round(shipping_lag, 1) if shipping_lag is not None else None
            ],
        }
    )


if __name__ == "__main__":
    print(order_lifecycle_frame())
