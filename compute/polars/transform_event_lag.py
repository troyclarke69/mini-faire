from __future__ import annotations

import polars as pl

from compute.polars.duckdb_frame import read_duckdb_frame
from ingestion.paths import DUCKDB_PATH


def event_lag_summary_frame(db_path=DUCKDB_PATH) -> pl.DataFrame:
    events = read_duckdb_frame(
        """
        select
          event_type,
          cast(event_ts as timestamp) as event_ts,
          cast(loaded_at as timestamp) as loaded_at
        from marts.fact_orders_events
        """,
        db_path,
    )

    return (
        events.with_columns(
            ((pl.col("loaded_at") - pl.col("event_ts")).dt.total_seconds()).alias("lag_seconds")
        )
        .group_by("event_type")
        .agg(
            pl.len().alias("event_count"),
            pl.col("lag_seconds").min().alias("min_lag_seconds"),
            pl.col("lag_seconds").mean().round(2).alias("avg_lag_seconds"),
            pl.col("lag_seconds").max().alias("max_lag_seconds"),
        )
        .sort("event_type")
    )


if __name__ == "__main__":
    print(event_lag_summary_frame())

