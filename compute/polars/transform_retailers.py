from __future__ import annotations

import polars as pl

from compute.polars.duckdb_frame import read_duckdb_frame
from ingestion.paths import DUCKDB_PATH


def retailer_cohort_retention_frame(db_path=DUCKDB_PATH) -> pl.DataFrame:
    retailer_orders = read_duckdb_frame(
        """
        select
          r.retailer_id,
          r.retailer_name,
          cast(r.signup_date as date) as signup_date,
          f.order_id,
          cast(f.order_ts as timestamp) as order_ts,
          f.net_amount
        from marts.dim_retailer r
        left join marts.fact_orders f on r.retailer_key = f.retailer_key
        """,
        db_path,
    )

    return (
        retailer_orders.with_columns(
            [
                pl.col("signup_date").dt.truncate("1mo").alias("signup_month"),
                pl.col("order_ts").dt.truncate("1mo").alias("order_month"),
            ]
        )
        .group_by("signup_month", "order_month")
        .agg(
            pl.col("retailer_id").n_unique().alias("active_retailers"),
            pl.col("order_id").n_unique().alias("order_count"),
            pl.col("net_amount").sum().alias("net_revenue"),
        )
        .with_columns(
            (
                (
                    pl.col("order_month").dt.year() * 12
                    + pl.col("order_month").dt.month()
                    - pl.col("signup_month").dt.year() * 12
                    - pl.col("signup_month").dt.month()
                )
            )
            .alias("cohort_age_months")
        )
        .sort(["signup_month", "cohort_age_months"])
    )


if __name__ == "__main__":
    print(retailer_cohort_retention_frame())

