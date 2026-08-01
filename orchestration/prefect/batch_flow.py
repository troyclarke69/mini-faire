from __future__ import annotations

try:
    from prefect import flow, task
except ImportError:
    flow = task = None

from ingestion.batch_ingestion import ingest_all_batches
from ingestion.load_duckdb import rebuild_warehouse


if task:

    @task(retries=2)
    def validate_batch_files():
        return ingest_all_batches()

    @task(retries=2)
    def load_raw_to_staging_and_elt():
        rebuild_warehouse()

    @flow(name="batch_marketplace_ingestion_elt")
    def batch_marketplace_ingestion_elt():
        validate_batch_files()
        load_raw_to_staging_and_elt()


if __name__ == "__main__":
    if flow is None:
        raise SystemExit("Install prefect to run this flow: pip install '.[orchestration]'")
    batch_marketplace_ingestion_elt()

