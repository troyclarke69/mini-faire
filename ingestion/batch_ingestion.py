from __future__ import annotations

from pathlib import Path

from ingestion.metadata import (
    IngestionRun,
    LineageEdge,
    duration_ms,
    file_sha256,
    upsert_ingestion_run,
    upsert_lineage_edges,
    utc_now,
    write_metadata,
)
from ingestion.paths import DATA_DIR, RAW_DIR
from ingestion.quarantine import write_valid_and_quarantine
from ingestion.validate import load_json_records, validate_records


BATCH_ENTITIES = ("retailers", "products", "orders")


def batch_run_id(entity: str, path: Path) -> str:
    partition = "_".join(path.parent.relative_to(DATA_DIR / "batch" / entity).parts)
    return f"batch_{entity}_{partition}_{path.stem}"


def ingest_batch_file(entity: str, path: Path, run_id: str | None = None) -> IngestionRun:
    started_at = utc_now()
    run_id = run_id or batch_run_id(entity, path)
    records = load_json_records(path)
    result = validate_records(entity, records)
    partition = path.parent.relative_to(DATA_DIR / "batch" / entity)
    base = RAW_DIR / "batch" / entity / partition / run_id
    valid_path = base / "valid" / path.name
    quarantine_path = base / "quarantine" / path.name
    metadata_path = base / "metadata" / "ingestion_run.json"

    write_valid_and_quarantine(
        valid_path=valid_path,
        quarantine_path=quarantine_path,
        valid_records=result.valid_records,
        invalid_records=result.invalid_records,
    )

    completed_at = utc_now()
    status = "success" if not result.invalid_records else "completed_with_quarantine"
    run = IngestionRun(
        run_id=run_id,
        source="batch",
        entity=entity,
        file_name=str(path),
        source_path=str(path),
        source_content_sha256=file_sha256(path),
        partition_path=str(partition),
        contract_name=f"{entity.rstrip('s')}.schema.json",
        valid_count=len(result.valid_records),
        invalid_count=len(result.invalid_records),
        schema_version="2020-12",
        valid_path=str(valid_path),
        quarantine_path=str(quarantine_path),
        metadata_path=str(metadata_path),
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms(started_at, completed_at),
        status=status,
    )
    write_metadata(metadata_path, run)
    upsert_ingestion_run(run)
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=run_id,
                source_node=str(path),
                target_node=str(valid_path),
                edge_type="validated_to_valid_raw",
                entity=entity,
                created_at=completed_at,
            ),
            LineageEdge(
                run_id=run_id,
                source_node=str(path),
                target_node=str(quarantine_path),
                edge_type="validated_to_quarantine",
                entity=entity,
                created_at=completed_at,
            ),
            LineageEdge(
                run_id=run_id,
                source_node=str(valid_path),
                target_node=f"raw.raw_{entity}",
                edge_type="loaded_to_raw_table",
                entity=entity,
                created_at=completed_at,
            ),
        ]
    )
    return run


def ingest_all_batches(root: Path = DATA_DIR / "batch") -> list[IngestionRun]:
    runs: list[IngestionRun] = []
    for entity in BATCH_ENTITIES:
        for path in sorted((root / entity).glob("**/*.json")):
            runs.append(ingest_batch_file(entity, path))
    return runs


if __name__ == "__main__":
    for ingestion_run in ingest_all_batches():
        print(ingestion_run)
