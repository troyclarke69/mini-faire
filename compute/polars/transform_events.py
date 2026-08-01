from __future__ import annotations

import polars as pl
import duckdb

from ingestion.paths import DUCKDB_PATH


def event_microbatch_summary(db_path=DUCKDB_PATH) -> pl.DataFrame:
    with duckdb.connect(str(db_path), read_only=True) as con:
        result = con.execute(
            """
            select
              event_type,
              cast(event_ts as timestamp) as event_ts,
              gross_amount,
              quantity
            from marts.fact_orders_events
            """
        )
        events = pl.DataFrame(result.fetchall(), schema=[col[0] for col in result.description], orient="row")
    return (
        events.with_columns(pl.col("event_ts").dt.truncate("5m").alias("microbatch_window"))
        .group_by("microbatch_window", "event_type")
        .agg(
            pl.len().alias("event_count"),
            pl.col("gross_amount").sum().alias("event_gmv"),
            pl.col("quantity").sum().alias("event_units"),
        )
        .sort("microbatch_window")
    )


if __name__ == "__main__":
    print(event_microbatch_summary())
