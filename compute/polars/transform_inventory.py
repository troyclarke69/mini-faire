from __future__ import annotations

import polars as pl

from compute.polars.duckdb_frame import read_duckdb_frame
from ingestion.paths import DUCKDB_PATH


def inventory_movement_frame(db_path=DUCKDB_PATH) -> pl.DataFrame:
    """Product-level movement derived from inventory_updated events - the
    event-driven counterpart to the order-driven inventory_velocity in
    marts.metrics_product_velocity."""
    events = read_duckdb_frame(
        """
        select
          p.product_id,
          p.product_name,
          p.product_category,
          e.delta,
          cast(e.event_ts as timestamp) as event_ts
        from marts.fact_product_events e
        left join marts.dim_product p on e.product_key = p.product_key
        where e.event_type = 'inventory_updated'
        """,
        db_path,
    )
    return (
        events.group_by("product_id", "product_name", "product_category")
        .agg(
            pl.len().alias("event_count"),
            pl.col("delta").sum().alias("total_delta"),
            pl.col("delta").mean().round(2).alias("avg_delta"),
            pl.col("event_ts").max().alias("last_updated"),
        )
        .sort("total_delta")
    )


if __name__ == "__main__":
    print(inventory_movement_frame())
