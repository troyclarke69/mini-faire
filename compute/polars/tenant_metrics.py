"""Tenant-aware compute (PHASE7-DEPLOYMENT.md Section 2).

Polars-side metrics computed on top of `warehouse/duckdb/tenant_elt.sql`'s
`marts.fact_tenant_orders` / `marts.metrics_tenant_daily` - the tenant
counterpart of `compute/polars/compute_metrics.py`'s single-tenant
`marts.compute_*` tables, reusing that module's `insert_compute_audit()`
bookkeeping (writes to `marts.compute_model_runs` / `elt_model_runs`) so
tenant compute runs show up in the same ELT/compute-run audit trail the
frontend's `/compute` page already reads, not a second untracked pipeline.

Two frames:

- `tenant_health_frame()`: one row per tenant - order volume, GMV, net
  revenue, and a 0-100 `tenant_health_score` blending recency and revenue
  (same shape and intent as `compute_metrics.py`'s per-retailer
  `retailer_health_score`, just aggregated one level up to "the whole
  tenant" - useful for a platform-admin view across tenants, e.g. a future
  `/observability` "tenant usage" panel per PHASE7-DEPLOYMENT.md Section 8).
- `tenant_growth_frame()`: a 7-day rolling GMV trend per tenant (today's
  7-day GMV vs the prior 7-day window), flagging tenants whose usage is
  declining - the kind of window computation that's naturally a Polars
  `rolling`/`shift` operation rather than a hand-rolled SQL window, matching
  why this repo already does trend/lag-style metrics in Polars
  (`transform_event_lag.py`, `transform_products.py`'s reorder risk) rather
  than in `warehouse/duckdb/*.sql`.
"""

from __future__ import annotations

import time

import duckdb
import polars as pl

from compute.polars.compute_metrics import insert_compute_audit
from compute.polars.duckdb_frame import read_duckdb_frame
from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import utc_now
from ingestion.paths import DUCKDB_PATH

GROWTH_WINDOW_DAYS = 7


def _timed_frame(builder, db_path):
    started_at = utc_now()
    t0 = time.monotonic()
    frame = builder(db_path)
    duration_ms = int((time.monotonic() - t0) * 1000)
    completed_at = utc_now()
    return frame, started_at, completed_at, duration_ms


def tenant_health_frame(db_path=DUCKDB_PATH) -> pl.DataFrame:
    orders = read_duckdb_frame(
        """
        select tenant_id, order_id, order_date, gross_amount, net_amount
        from marts.fact_tenant_orders
        """,
        db_path,
    )
    if orders.height == 0:
        return pl.DataFrame(
            schema={
                "tenant_id": pl.Utf8, "order_count": pl.Int64, "gmv": pl.Float64,
                "net_revenue": pl.Float64, "last_order_date": pl.Date, "tenant_health_score": pl.Float64,
            }
        )

    today = orders.select(pl.col("order_date").max()).item()
    return (
        orders.group_by("tenant_id")
        .agg(
            pl.col("order_id").n_unique().alias("order_count"),
            pl.col("gross_amount").sum().alias("gmv"),
            pl.col("net_amount").sum().alias("net_revenue"),
            pl.col("order_date").max().alias("last_order_date"),
        )
        .with_columns(
            (today - pl.col("last_order_date")).dt.total_days().alias("_days_since_last_order")
        )
        .with_columns(
            (
                (100.0 - pl.col("_days_since_last_order").clip(0, 100))
                * 0.5
                + ((pl.col("order_count").cast(pl.Float64) + 1.0).log() * 10).clip(0, 50)
            )
            .clip(0, 100)
            .alias("tenant_health_score")
        )
        .drop("_days_since_last_order")
        .select("tenant_id", "order_count", "gmv", "net_revenue", "last_order_date", "tenant_health_score")
        .sort("tenant_id")
    )


def tenant_growth_frame(db_path=DUCKDB_PATH) -> pl.DataFrame:
    # The trailing/prior 7-day GMV windows are computed in SQL (DuckDB's
    # `rows between N preceding and current row` window frame is the
    # well-established way to express this, same as
    # marts.metrics_retailer_daily's own aggregations) rather than a Polars
    # rolling-window expression, which keeps the one part of this frame that
    # genuinely needs verifying against a live engine inside DuckDB (this
    # sandbox has neither DuckDB nor Polars installed to test against - see
    # this module's delivery notes) down to plain, long-standing ANSI SQL.
    # Polars below only does the growth-rate/trend labeling, which is a
    # handful of `when/then/otherwise` comparisons.
    windowed = read_duckdb_frame(
        f"""
        select
          tenant_id,
          order_date as as_of_date,
          sum(gmv) over (
            partition by tenant_id order by order_date
            rows between {GROWTH_WINDOW_DAYS - 1} preceding and current row
          ) as trailing_7d_gmv,
          sum(gmv) over (
            partition by tenant_id order by order_date
            rows between {2 * GROWTH_WINDOW_DAYS - 1} preceding and {GROWTH_WINDOW_DAYS} preceding
          ) as prior_7d_gmv
        from marts.metrics_tenant_daily
        qualify row_number() over (partition by tenant_id order by order_date desc) = 1
        """,
        db_path,
    )
    if windowed.height == 0:
        return pl.DataFrame(
            schema={
                "tenant_id": pl.Utf8, "as_of_date": pl.Date, "trailing_7d_gmv": pl.Float64,
                "prior_7d_gmv": pl.Float64, "growth_rate": pl.Float64, "trend": pl.Utf8,
            }
        )

    latest = windowed.with_columns(pl.col("prior_7d_gmv").fill_null(0.0))
    return (
        latest.with_columns(
            pl.when(pl.col("prior_7d_gmv") > 0)
            .then((pl.col("trailing_7d_gmv") - pl.col("prior_7d_gmv")) / pl.col("prior_7d_gmv"))
            .otherwise(None)
            .alias("growth_rate")
        )
        .with_columns(
            pl.when(pl.col("growth_rate").is_null())
            .then(pl.lit("insufficient_history"))
            .when(pl.col("growth_rate") > 0.05)
            .then(pl.lit("growing"))
            .when(pl.col("growth_rate") < -0.05)
            .then(pl.lit("declining"))
            .otherwise(pl.lit("flat"))
            .alias("trend")
        )
        .select("tenant_id", "as_of_date", "trailing_7d_gmv", "prior_7d_gmv", "growth_rate", "trend")
        .sort("tenant_id")
    )


def _ensure_tenant_compute_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        create table if not exists marts.compute_tenant_health (
          tenant_id varchar,
          order_count integer,
          gmv double,
          net_revenue double,
          last_order_date date,
          tenant_health_score double
        )
        """
    )
    con.execute(
        """
        create table if not exists marts.compute_tenant_growth (
          tenant_id varchar,
          as_of_date date,
          trailing_7d_gmv double,
          prior_7d_gmv double,
          growth_rate double,
          trend varchar
        )
        """
    )


def persist_tenant_metrics(db_path=DUCKDB_PATH) -> None:
    health, health_started, health_completed, health_ms = _timed_frame(tenant_health_frame, db_path)
    growth, growth_started, growth_completed, growth_ms = _timed_frame(tenant_growth_frame, db_path)

    with connect_with_retry(db_path) as con:
        _ensure_tenant_compute_tables(con)

        con.execute("delete from marts.compute_tenant_health")
        con.executemany(
            "insert into marts.compute_tenant_health values (?, ?, ?, ?, ?, ?)",
            [
                (row["tenant_id"], row["order_count"], row["gmv"], row["net_revenue"],
                 row["last_order_date"], row["tenant_health_score"])
                for row in health.rows(named=True)
            ],
        )
        insert_compute_audit(
            con, model_name="tenant_health", target_table="marts.compute_tenant_health",
            source_tables="marts.fact_tenant_orders", frame=health,
            started_at=health_started, completed_at=health_completed, duration_ms=health_ms,
        )

        con.execute("delete from marts.compute_tenant_growth")
        con.executemany(
            "insert into marts.compute_tenant_growth values (?, ?, ?, ?, ?, ?)",
            [
                (row["tenant_id"], row["as_of_date"], row["trailing_7d_gmv"],
                 row["prior_7d_gmv"], row["growth_rate"], row["trend"])
                for row in growth.rows(named=True)
            ],
        )
        insert_compute_audit(
            con, model_name="tenant_growth", target_table="marts.compute_tenant_growth",
            source_tables="marts.metrics_tenant_daily", frame=growth,
            started_at=growth_started, completed_at=growth_completed, duration_ms=growth_ms,
        )


if __name__ == "__main__":
    persist_tenant_metrics()
    print("Persisted tenant compute metrics")
