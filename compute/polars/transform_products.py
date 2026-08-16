from __future__ import annotations

import polars as pl

from compute.polars.duckdb_frame import read_duckdb_frame
from ingestion.paths import DUCKDB_PATH


def product_reorder_frame(db_path=DUCKDB_PATH) -> pl.DataFrame:
    product_activity = read_duckdb_frame(
        """
        select
          p.product_id,
          p.product_name,
          p.brand_id,
          p.product_category,
          p.inventory_count,
          coalesce(sum(f.quantity), 0)::integer as units_sold,
          max(cast(f.order_ts as timestamp)) as last_sold_at
        from marts.dim_product p
        left join marts.fact_orders f on p.product_key = f.product_key
        group by
          p.product_id,
          p.product_name,
          p.brand_id,
          p.product_category,
          p.inventory_count
        """,
        db_path,
    )

    return (
        product_activity.with_columns(
            [
                (pl.col("units_sold") / pl.when(pl.col("inventory_count") == 0).then(1).otherwise(pl.col("inventory_count")))
                .round(4)
                .alias("sell_through_rate"),
                (
                    (pl.col("units_sold") * 12)
                    + ((100 - pl.col("inventory_count")).clip(0, 100) * 0.8)
                )
                .round(2)
                .alias("reorder_risk_score"),
            ]
        )
        .with_columns(
            pl.when(pl.col("reorder_risk_score") >= 80)
            .then(pl.lit("high"))
            .when(pl.col("reorder_risk_score") >= 40)
            .then(pl.lit("medium"))
            .otherwise(pl.lit("low"))
            .alias("reorder_risk_band")
        )
        .sort("reorder_risk_score", descending=True)
    )


def brand_contribution_frame(db_path=DUCKDB_PATH) -> pl.DataFrame:
    order_products = read_duckdb_frame(
        """
        select
          p.brand_id,
          p.product_category,
          f.order_id,
          f.quantity,
          f.gross_amount,
          f.net_amount,
          f.estimated_profit
        from marts.fact_orders f
        left join marts.dim_product p on f.product_key = p.product_key
        """,
        db_path,
    )

    return (
        order_products.group_by("brand_id", "product_category")
        .agg(
            pl.col("order_id").n_unique().alias("order_count"),
            pl.col("quantity").sum().alias("units_sold"),
            pl.col("gross_amount").sum().alias("gmv"),
            pl.col("net_amount").sum().alias("net_revenue"),
            pl.col("estimated_profit").sum().alias("estimated_profit"),
        )
        .with_columns(
            (
                pl.col("estimated_profit")
                / pl.when(pl.col("net_revenue") == 0).then(None).otherwise(pl.col("net_revenue"))
            )
            .round(4)
            .alias("estimated_margin")
        )
        .sort("gmv", descending=True)
    )


if __name__ == "__main__":
    print(product_reorder_frame())
    print(brand_contribution_frame())

