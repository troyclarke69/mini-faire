from __future__ import annotations

from datetime import datetime

try:
    from airflow.decorators import dag, task
except ImportError:
    dag = task = None

from ingestion.event_ingestion import ingest_all_events
from ingestion.load_duckdb import rebuild_warehouse


if dag:

    @dag(
        dag_id="events_order_microbatch_elt",
        schedule="*/5 * * * *",
        start_date=datetime(2026, 7, 31),
        catchup=False,
        default_args={"retries": 2},
        tags=["mini-faire", "events", "duckdb"],
    )
    def events_order_microbatch_elt():
        @task
        def read_event_batch():
            return "order_created"

        @task
        def validate_events(_event_type):
            return [run.run_id for run in ingest_all_events()]

        @task
        def load_events_to_staging_and_warehouse(_runs):
            rebuild_warehouse()

        load_events_to_staging_and_warehouse(validate_events(read_event_batch()))

    events_order_microbatch_elt()

