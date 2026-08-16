from __future__ import annotations

import polars as pl

from compute.polars.duckdb_frame import read_duckdb_frame
from ingestion.paths import DUCKDB_PATH


def order_health_frame(db_path=DUCKDB_PATH) -> pl.DataFrame:
    orders = read_duckdb_frame(
        """
        select
          retailer_id,
          net_amount,
          estimated_profit,
          cast(order_ts as timestamp) as order_ts
        from marts.fact_orders
        """,
        db_path,
    )
    return (
        orders.group_by("retailer_id")
        .agg(
            pl.len().alias("order_count"),
            pl.col("net_amount").sum().alias("net_revenue"),
            pl.col("estimated_profit").sum().alias("estimated_profit"),
            pl.col("order_ts").max().alias("last_order_ts"),
        )
        .with_columns(
            (
                (pl.col("order_count") * 10)
                + (pl.col("net_revenue") / 50)
                + (pl.col("estimated_profit") / 25)
            )
            .round(2)
            .alias("retailer_health_score")
        )
        .sort("retailer_health_score", descending=True)
    )


if __name__ == "__main__":
    print(order_health_frame())
