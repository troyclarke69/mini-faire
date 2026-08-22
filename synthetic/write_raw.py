"""Writes a GeneratedDataset (see synthetic/generator.py) into the repo's raw
source layout:

  data/batch/<entity>/YYYY/MM/DD/<entity>.json
  data/events/<event_type>/YYYY/MM/DD/HH/events.json

This mirrors exactly what a human or upstream system dropping daily snapshots
/ hourly micro-batches would produce. It intentionally does NOT run
validation, write to data/raw/, or emit ingestion_runs/lineage_edges rows -
that is all owned by ingestion/batch_ingestion.py and ingestion/event_ingestion.py
so there is exactly one place that logic lives. orchestration/synthetic_flow.py
(and scripts/run_demo.py, which already globs everything under data/) is what
turns these freshly-written source files into metadata + lineage by running
the normal ingestion pipeline against them.
"""

from __future__ import annotations

import json
from pathlib import Path

from ingestion.paths import DATA_DIR
from synthetic.generator import GeneratedDataset


def _write_json(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def write_batch_files(dataset: GeneratedDataset, data_dir: Path = DATA_DIR) -> list[Path]:
    written: list[Path] = []
    entity_buckets = {
        "retailers": dataset.retailers_by_day,
        "products": dataset.products_by_day,
        "orders": dataset.orders_by_day,
    }
    for entity, by_day in entity_buckets.items():
        for day_iso, records in by_day.items():
            year, month, day = day_iso.split("-")
            path = data_dir / "batch" / entity / year / month / day / f"{entity}.json"
            _write_json(path, records)
            written.append(path)
    return written


def write_event_files(dataset: GeneratedDataset, data_dir: Path = DATA_DIR) -> list[Path]:
    written: list[Path] = []
    for event_type, by_day in dataset.events.items():
        for day_iso, by_hour in by_day.items():
            year, month, day = day_iso.split("-")
            for hour, records in by_hour.items():
                path = (
                    data_dir
                    / "events"
                    / event_type
                    / year
                    / month
                    / day
                    / f"{hour:02d}"
                    / "events.json"
                )
                _write_json(path, records)
                written.append(path)
    return written


def write_dataset(dataset: GeneratedDataset, data_dir: Path = DATA_DIR) -> dict[str, list[Path]]:
    return {
        "batch": write_batch_files(dataset, data_dir),
        "events": write_event_files(dataset, data_dir),
    }


if __name__ == "__main__":
    import yaml

    from ingestion.paths import PROJECT_ROOT
    from synthetic.generator import generate_dataset

    with (PROJECT_ROOT / "config" / "synthetic.yaml").open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    generated = generate_dataset(config)
    result = write_dataset(generated)
    print(f"Wrote {len(result['batch'])} batch files and {len(result['events'])} event files.")
    print("Record summary:", dict(generated.summary))
