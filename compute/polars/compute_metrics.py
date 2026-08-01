from __future__ import annotations

import duckdb

from compute.polars.transform_events import event_microbatch_summary
from compute.polars.transform_orders import order_health_frame
from ingestion.paths import DUCKDB_PATH


def persist_compute_metrics(db_path=DUCKDB_PATH) -> None:
    health = order_health_frame(db_path)
    event_summary = event_microbatch_summary(db_path)
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


if __name__ == "__main__":
    persist_compute_metrics()
    print("Persisted Polars-derived metric tables")
