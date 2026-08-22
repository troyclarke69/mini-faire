from __future__ import annotations

import time

import duckdb
import polars as pl

from ingestion.duckdb_utils import connect_with_retry
from compute.polars.transform_event_lag import event_lag_summary_frame
from compute.polars.transform_events import event_microbatch_summary
from compute.polars.transform_inventory import inventory_movement_frame
from compute.polars.transform_lifecycle import order_lifecycle_frame
from compute.polars.transform_orders import order_health_frame
from compute.polars.transform_products import brand_contribution_frame, product_reorder_frame
from compute.polars.transform_retailers import retailer_cohort_retention_frame
from ingestion.metadata import utc_now
from ingestion.paths import DUCKDB_PATH


def _timed_frame(builder, db_path):
    """Wraps a compute/polars/transform_*.py frame builder with wall-clock
    timing (PHASE5-MONITORING.md Section 3's "compute run duration" metric -
    see monitoring/metrics.py). Timed here rather than around the DuckDB
    insert below because the actual Polars computation - reading from DuckDB,
    joining/aggregating in Polars - all happens inside the builder call,
    before any connection in persist_compute_metrics() is even opened."""
    started_at = utc_now()
    t0 = time.monotonic()
    frame = builder(db_path)
    duration_ms = int((time.monotonic() - t0) * 1000)
    completed_at = utc_now()
    return frame, started_at, completed_at, duration_ms


def insert_compute_audit(
    con: duckdb.DuckDBPyConnection,
    *,
    model_name: str,
    target_table: str,
    source_tables: str,
    frame: pl.DataFrame,
    started_at: str,
    completed_at: str,
    duration_ms: int,
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
    # Phase 5 (PHASE5-MONITORING.md Section 3, "compute run duration"):
    # additive columns, migrated in for warehouses built before this existed -
    # same `alter table ... add column if not exists` pattern
    # ingestion/metadata.py's upsert_ingestion_run() already uses.
    # information_schema.columns (rather than `pragma table_info(...)`, which
    # ingestion/metadata.py uses for an unqualified table) reliably handles
    # this schema-qualified table name across DuckDB versions.
    existing_columns = {
        row[0]
        for row in con.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'marts' and table_name = 'compute_model_runs'"
        ).fetchall()
    }
    for column_name, column_type in {"started_at": "timestamptz", "duration_ms": "integer"}.items():
        if column_name not in existing_columns:
            con.execute(f"alter table marts.compute_model_runs add column {column_name} {column_type}")

    con.execute(
        """
        insert into marts.compute_model_runs (
          model_name, target_table, source_tables, row_count, column_count,
          computed_at, status, started_at, duration_ms
        )
        values (?, ?, ?, ?, ?, ?, 'success', ?, ?)
        """,
        [model_name, target_table, source_tables, frame.height, len(frame.columns), completed_at, started_at, duration_ms],
    )
    # PHASE4-REALTIME&STREAMING.md Section 4B: "append compute runs to
    # elt_model_runs" (in addition to marts.compute_model_runs above), so the
    # frontend's ELT Model Runs table/API (which only reads elt_model_runs)
    # shows Polars compute activity too, not just DuckDB SQL model runs.
    # elt_model_runs' schema was designed for delete-insert SQL models
    # (business_key, high_watermark); compute models don't have a natural
    # key or watermark of their own, so those columns get a clearly-labeled
    # placeholder rather than a fabricated value.
    #
    # `create table if not exists` here (matching warehouse/duckdb/init.sql's
    # definition exactly) is defensive, not the primary creation path: every
    # real call site runs rebuild_warehouse() - which executes init.sql -
    # before persist_compute_metrics(), so the table already exists in
    # practice. This just keeps `python -m compute.polars.compute_metrics`
    # (this file's own __main__) safe to run standalone against an
    # already-built warehouse.
    con.execute(
        """
        create table if not exists elt_model_runs (
          model_name varchar,
          target_table varchar,
          load_strategy varchar,
          business_key varchar,
          source_row_count integer,
          affected_key_count integer,
          target_row_count integer,
          high_watermark timestamptz,
          started_at timestamptz,
          completed_at timestamptz,
          status varchar
        )
        """
    )
    # Phase 5: started_at/completed_at are now the real wall-clock bounds of
    # this model's Polars computation (see _timed_frame above) rather than
    # two `current_timestamp` reads inside the same INSERT statement, which
    # always evaluated to the same instant and made every Polars-derived
    # elt_model_runs row look like it took 0ms - silently skewing
    # monitoring/metrics.py's elt_run_duration_avg_seconds toward zero.
    con.execute(
        """
        insert into elt_model_runs
        values (?, ?, 'polars_full_refresh', 'n/a', ?, ?, ?, null, ?, ?, 'success')
        """,
        [model_name, target_table, frame.height, frame.height, frame.height, started_at, completed_at],
    )


def persist_compute_metrics(db_path=DUCKDB_PATH) -> None:
    health, health_started, health_completed, health_ms = _timed_frame(order_health_frame, db_path)
    event_summary, event_summary_started, event_summary_completed, event_summary_ms = _timed_frame(
        event_microbatch_summary, db_path
    )
    product_reorder, product_reorder_started, product_reorder_completed, product_reorder_ms = _timed_frame(
        product_reorder_frame, db_path
    )
    brand_contribution, brand_contribution_started, brand_contribution_completed, brand_contribution_ms = _timed_frame(
        brand_contribution_frame, db_path
    )
    cohort_retention, cohort_retention_started, cohort_retention_completed, cohort_retention_ms = _timed_frame(
        retailer_cohort_retention_frame, db_path
    )
    event_lag, event_lag_started, event_lag_completed, event_lag_ms = _timed_frame(event_lag_summary_frame, db_path)
    inventory_movement, inventory_movement_started, inventory_movement_completed, inventory_movement_ms = _timed_frame(
        inventory_movement_frame, db_path
    )
    order_lifecycle, order_lifecycle_started, order_lifecycle_completed, order_lifecycle_ms = _timed_frame(
        order_lifecycle_frame, db_path
    )
    with connect_with_retry(db_path) as con:
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
            started_at=health_started,
            completed_at=health_completed,
            duration_ms=health_ms,
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
            started_at=event_summary_started,
            completed_at=event_summary_completed,
            duration_ms=event_summary_ms,
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
            started_at=product_reorder_started,
            completed_at=product_reorder_completed,
            duration_ms=product_reorder_ms,
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
            started_at=brand_contribution_started,
            completed_at=brand_contribution_completed,
            duration_ms=brand_contribution_ms,
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
            started_at=cohort_retention_started,
            completed_at=cohort_retention_completed,
            duration_ms=cohort_retention_ms,
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
            started_at=event_lag_started,
            completed_at=event_lag_completed,
            duration_ms=event_lag_ms,
        )

        con.execute(
            """
            create or replace table marts.compute_inventory_movement (
              product_id varchar,
              product_name varchar,
              product_category varchar,
              event_count integer,
              total_delta integer,
              avg_delta double,
              last_updated timestamp
            )
            """
        )
        con.executemany(
            """
            insert into marts.compute_inventory_movement
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["product_id"],
                    row["product_name"],
                    row["product_category"],
                    row["event_count"],
                    row["total_delta"],
                    row["avg_delta"],
                    row["last_updated"],
                )
                for row in inventory_movement.rows(named=True)
            ],
        )
        insert_compute_audit(
            con,
            model_name="inventory_movement",
            target_table="marts.compute_inventory_movement",
            source_tables="marts.fact_product_events,marts.dim_product",
            frame=inventory_movement,
            started_at=inventory_movement_started,
            completed_at=inventory_movement_completed,
            duration_ms=inventory_movement_ms,
        )

        con.execute(
            """
            create or replace table marts.compute_order_lifecycle (
              created_count integer,
              paid_count integer,
              shipped_count integer,
              paid_rate double,
              shipped_rate double,
              avg_payment_lag_seconds double,
              avg_shipping_lag_seconds double
            )
            """
        )
        con.executemany(
            """
            insert into marts.compute_order_lifecycle
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["created_count"],
                    row["paid_count"],
                    row["shipped_count"],
                    row["paid_rate"],
                    row["shipped_rate"],
                    row["avg_payment_lag_seconds"],
                    row["avg_shipping_lag_seconds"],
                )
                for row in order_lifecycle.rows(named=True)
            ],
        )
        insert_compute_audit(
            con,
            model_name="order_lifecycle",
            target_table="marts.compute_order_lifecycle",
            source_tables="marts.fact_orders_events",
            frame=order_lifecycle,
            started_at=order_lifecycle_started,
            completed_at=order_lifecycle_completed,
            duration_ms=order_lifecycle_ms,
        )


if __name__ == "__main__":
    persist_compute_metrics()
    print("Persisted Polars-derived metric tables")
