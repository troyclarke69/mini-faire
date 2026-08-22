"""Orchestration entry point for MongoDB ingestion (PHASE3-DATA-LOAD.md
Section 2D): read config/mongo.yaml, pull new documents from every configured
collection, write raw JSON, then trigger the normal ELT + compute rebuild -
the same steps scripts/run_demo.py runs, so newly-pulled Mongo data is
immediately queryable.

Requires the optional `mongo` dependency group (`pip install -e ".[mongo]"`)
and a MONGO_PASSWORD environment variable - see config/mongo.yaml for details.
Never hardcode the password here or anywhere else.

Run with:
  .\\.venv\\Scripts\\python.exe -m orchestration.mongo_flow
"""

from __future__ import annotations

from pathlib import Path


def run_mongo_flow(config_path: Path | None = None) -> None:
    from ingestion.mongo_ingest import MONGO_CONFIG_PATH, ingest_all_mongo_collections, load_mongo_config

    config = load_mongo_config(config_path or MONGO_CONFIG_PATH)
    runs = ingest_all_mongo_collections(config)
    total_valid = sum(run.valid_count for run in runs)
    total_invalid = sum(run.invalid_count for run in runs)
    print(f"Mongo ingestion runs: {len(runs)} (valid={total_valid}, quarantined={total_invalid})")
    for run in runs:
        print(f"  {run.entity}: valid={run.valid_count} invalid={run.invalid_count} status={run.status}")

    if not runs:
        print("No new documents pulled from MongoDB this run (all collections at their watermark).")
        return

    from compute.polars.compute_metrics import persist_compute_metrics
    from ingestion.load_duckdb import rebuild_warehouse

    rebuild_warehouse()
    persist_compute_metrics()
    print("Warehouse + compute metrics rebuilt with newly ingested MongoDB data.")


if __name__ == "__main__":
    run_mongo_flow()
