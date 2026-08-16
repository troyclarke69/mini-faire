from __future__ import annotations

import duckdb
import polars as pl

from compute.polars.transform_event_lag import event_lag_summary_frame
from compute.polars.transform_events import event_microbatch_summary
from compute.polars.transform_orders import order_health_frame
from compute.polars.transform_products import brand_contribution_frame, product_reorder_frame
from compute.polars.transform_retailers import retailer_cohort_retention_frame
from ingestion.paths import DUCKDB_PATH


def insert_compute_audit(
    con: duckdb.DuckDBPyConnection,
    *,
    model_name: str,
    target_table: str,
    source_tables: str,
    frame: pl.DataFrame,
) -> None:
    con.execute(
        """
        create table if not exists marts.compute_model_runs (
          model_name varchar,
          target_table varchar,
          source_tables varchar,
          row_count integer,
          column_count integer,
          computed_at timestamptz,
          status varchar
        )
        """
    )
    con.execute(
        """
        insert into marts.compute_model_runs
        values (?, ?, ?, ?, ?, current_timestamp, 'success')
        """,
        [model_name, target_table, source_tables, frame.height, len(frame.columns)],
    )


def persist_compute_metrics(db_path=DUCKDB_PATH) -> None:
    health = order_health_frame(db_path)
    event_summary = event_microbatch_summary(db_path)
    product_reorder = product_reorder_frame(db_path)
    brand_contribution = brand_contribution_frame(db_path)
    cohort_retention = retailer_cohort_retention_frame(db_path)
    event_lag = event_lag_summary_frame(db_path)
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            """
            create or replace table marts.compute_retailer_health (
              retailer_id varchar,
              order_count integer,
              net_revenue double,
              estimated_profit double,
              last_order_ts timestamp,
              retailer_health_score double
            )
            """
        )
        con.executemany(
            """
            insert into marts.compute_retailer_health
            values (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["retailer_id"],
                    row["order_count"],
                    row["net_revenue"],
                    row["estimated_profit"],
                    row["last_order_ts"],
                    row["retailer_health_score"],
                )
                for row in health.rows(named=True)
            ],
        )
        insert_compute_audit(
            con,
            model_name="retailer_health",
            target_table="marts.compute_retailer_health",
            source_tables="marts.fact_orders",
            frame=health,
        )

        con.execute(
            """
            create or replace table marts.compute_event_microbatch_summary (
              microbatch_window timestamp,
              event_type varchar,
              event_count integer,
              event_gmv double,
              event_units integer
            )
            """
        )
        con.executemany(
            """
            insert into marts.compute_event_microbatch_summary
            values (?, ?, ?, ?, ?)
            """,
            [
                (
                    row["microbatch_window"],
                    row["event_type"],
                    row["event_count"],
                    row["event_gmv"],
                    row["event_units"],
                )
                for row in event_summary.rows(named=True)
            ],
        )
        insert_compute_audit(
            con,
            model_name="event_microbatch_summary",
            target_table="marts.compute_event_microbatch_summary",
            source_tables="marts.fact_orders_events",
            frame=event_summary,
        )

        con.execute(
            """
            create or replace table marts.compute_product_reorder_risk (
              product_id varchar,
              product_name varchar,
              brand_id varchar,
              product_category varchar,
              inventory_count integer,
              units_sold integer,
              last_sold_at timestamp,
              sell_through_rate double,
              reorder_risk_score double,
              reorder_risk_band varchar
            )
            """
        )
        con.executemany(
            """
            insert into marts.compute_product_reorder_risk
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["product_id"],
                    row["product_name"],
                    row["brand_id"],
                    row["product_category"],
                    row["inventory_count"],
                    row["units_sold"],
                    row["last_sold_at"],
                    row["sell_through_rate"],
                    row["reorder_risk_score"],
                    row["reorder_risk_band"],
                )
                for row in product_reorder.rows(named=True)
            ],
        )
        insert_compute_audit(
            con,
            model_name="product_reorder_risk",
            target_table="marts.compute_product_reorder_risk",
            source_tables="marts.dim_product,marts.fact_orders",
            frame=product_reorder,
        )

        con.execute(
            """
            create or replace table marts.compute_brand_contribution (
              brand_id varchar,
              product_category varchar,
              order_count integer,
              units_sold integer,
              gmv double,
              net_revenue double,
              estimated_profit double,
              estimated_margin double
            )
            """
        )
        con.executemany(
            """
            insert into marts.compute_brand_contribution
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["brand_id"],
                    row["product_category"],
                    row["order_count"],
                    row["units_sold"],
                    row["gmv"],
                    row["net_revenue"],
                    row["estimated_profit"],
                    row["estimated_margin"],
                )
                for row in brand_contribution.rows(named=True)
            ],
        )
        insert_compute_audit(
            con,
            model_name="brand_contribution",
            target_table="marts.compute_brand_contribution",
            source_tables="marts.dim_product,marts.fact_orders",
            frame=brand_contribution,
        )

        con.execute(
            """
            create or replace table marts.compute_retailer_cohort_retention (
              signup_month date,
              order_month date,
              active_retailers integer,
              order_count integer,
              net_revenue double,
              cohort_age_months integer
            )
            """
        )
        con.executemany(
            """
            insert into marts.compute_retailer_cohort_retention
            values (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["signup_month"],
                    row["order_month"],
                    row["active_retailers"],
                    row["order_count"],
                    row["net_revenue"],
                    row["cohort_age_months"],
                )
                for row in cohort_retention.rows(named=True)
            ],
        )
        insert_compute_audit(
            con,
            model_name="retailer_cohort_retention",
            target_table="marts.compute_retailer_cohort_retention",
            source_tables="marts.dim_retailer,marts.fact_orders",
            frame=cohort_retention,
        )

        con.execute(
            """
            create or replace table marts.compute_event_lag_summary (
              event_type varchar,
              event_count integer,
              min_lag_seconds double,
              avg_lag_seconds double,
              max_lag_seconds double
            )
            """
        )
        con.executemany(
            """
            insert into marts.compute_event_lag_summary
            values (?, ?, ?, ?, ?)
            """,
            [
                (
                    row["event_type"],
                    row["event_count"],
                    row["min_lag_seconds"],
                    row["avg_lag_seconds"],
                    row["max_lag_seconds"],
                )
                for row in event_lag.rows(named=True)
            ],
        )
        insert_compute_audit(
            con,
            model_name="event_lag_summary",
            target_table="marts.compute_event_lag_summary",
            source_tables="marts.fact_orders_events",
            frame=event_lag,
        )


if __name__ == "__main__":
    persist_compute_metrics()
    print("Persisted Polars-derived metric tables")
