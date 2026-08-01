from __future__ import annotations

try:
    from prefect import flow, task
except ImportError:
    flow = task = None

from ingestion.event_ingestion import ingest_all_events
from ingestion.load_duckdb import rebuild_warehouse


if task:

    @task(retries=2)
    def validate_events():
        return ingest_all_events()

    @task(retries=2)
    def load_events_to_staging_and_elt():
        rebuild_warehouse()

    @flow(name="events_order_microbatch_elt")
    def events_order_microbatch_elt():
        validate_events()
        load_events_to_staging_and_elt()


if __name__ == "__main__":
    if flow is None:
        raise SystemExit("Install prefect to run this flow: pip install '.[orchestration]'")
    events_order_microbatch_elt()

