"""Orchestration entry point for the synthetic data generator
(PHASE3-DATA-LOAD.md Section 3D): read config/synthetic.yaml, generate a
dataset, write it into the raw source layout, then trigger the normal
ingestion pipeline (validate, quarantine, metadata, lineage) plus a
warehouse/compute rebuild - the same steps scripts/run_demo.py runs, just
triggered right after this generation pass so the result is immediately
queryable.

Run with:
  .\\.venv\\Scripts\\python.exe -m orchestration.synthetic_flow
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ingestion.paths import PROJECT_ROOT


def run_synthetic_flow(config_path: Path | None = None) -> None:
    from synthetic.generator import generate_dataset
    from synthetic.write_raw import write_dataset

    config_path = config_path or PROJECT_ROOT / "config" / "synthetic.yaml"
    with open(config_path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    dataset = generate_dataset(config)
    written = write_dataset(dataset)
    print(f"Generated dataset: {dict(dataset.summary)}")
    print(f"Wrote {len(written['batch'])} batch files and {len(written['events'])} event files.")

    from compute.polars.compute_metrics import persist_compute_metrics
    from ingestion.batch_ingestion import ingest_all_batches
    from ingestion.event_ingestion import ingest_all_events
    from ingestion.load_duckdb import rebuild_warehouse

    batch_runs = ingest_all_batches()
    event_runs = ingest_all_events()
    rebuild_warehouse()
    persist_compute_metrics()

    print(f"Batch ingestion runs: {len(batch_runs)}")
    print(f"Event ingestion runs: {len(event_runs)}")


if __name__ == "__main__":
    run_synthetic_flow()
