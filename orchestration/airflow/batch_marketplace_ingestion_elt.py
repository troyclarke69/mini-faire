from __future__ import annotations

from datetime import datetime

try:
    from airflow.decorators import dag, task
except ImportError:
    dag = task = None

from ingestion.batch_ingestion import ingest_all_batches
from ingestion.load_duckdb import rebuild_warehouse


if dag:

    @dag(
        dag_id="batch_marketplace_ingestion_elt",
        schedule="0 2 * * *",
        start_date=datetime(2026, 7, 31),
        catchup=False,
        default_args={"retries": 2},
        tags=["mini-faire", "batch", "duckdb"],
    )
    def batch_marketplace_ingestion_elt():
        @task
        def detect_new_files():
            return "sample-data"

        @task
        def validate_batch_files(_detected):
            return [run.run_id for run in ingest_all_batches()]

        @task
        def load_raw_to_staging_and_warehouse(_runs):
            rebuild_warehouse()

        load_raw_to_staging_and_warehouse(validate_batch_files(detect_new_files()))

    batch_marketplace_ingestion_elt()

