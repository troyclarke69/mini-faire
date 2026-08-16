from __future__ import annotations

from preflight import run_preflight


def main() -> None:
    run_preflight()

    from compute.polars.compute_metrics import persist_compute_metrics
    from ingestion.batch_ingestion import ingest_all_batches
    from ingestion.event_ingestion import ingest_all_events
    from ingestion.load_duckdb import rebuild_warehouse
    from ingestion.paths import DUCKDB_PATH

    batch_runs = ingest_all_batches()
    event_runs = ingest_all_events()
    rebuild_warehouse()
    persist_compute_metrics()

    print(f"Batch ingestion runs: {len(batch_runs)}")
    print(f"Event ingestion runs: {len(event_runs)}")
    print(f"DuckDB warehouse: {DUCKDB_PATH}")


if __name__ == "__main__":
    main()
