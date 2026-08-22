"""MongoDB ingestion module (PHASE3-DATA-LOAD.md Section 2B).

Pulls documents from the MongoDB collections configured in config/mongo.yaml,
writes each document as an individual raw JSON file, then runs it through the
exact same validate -> quarantine -> metadata -> lineage machinery that
ingestion/batch_ingestion.py and ingestion/event_ingestion.py use for
file-based sources, so Mongo is just another upstream feeding the same raw
zone (see ingestion/load_duckdb.py's RAW_TABLE_SOURCES, which already globs
`data/raw/<entity>/**/valid/*.json` alongside the batch/event zones).

SECURITY: the MongoDB password is never hardcoded here or in config/mongo.yaml.
It is read exclusively from the MONGO_PASSWORD environment variable at
connect time. If it is unset, connecting raises immediately rather than
silently using a blank/placeholder credential.

pymongo is an optional dependency (`pip install -e ".[mongo]"`) and is only
imported inside functions that actually need a live connection, so the rest
of the ingestion pipeline (and tests) work fine without it installed.

Run with:
  .\\.venv\\Scripts\\python.exe -m ingestion.mongo_ingest
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

import yaml

from ingestion.metadata import (
    IngestionRun,
    LineageEdge,
    duration_ms,
    upsert_ingestion_run,
    upsert_lineage_edges,
    utc_now,
    write_metadata,
)
from ingestion.paths import DATA_DIR, PROJECT_ROOT, RAW_DIR
from ingestion.quarantine import write_json
from ingestion.validate import CONTRACT_BY_ENTITY, validate_records

MONGO_CONFIG_PATH = PROJECT_ROOT / "config" / "mongo.yaml"

# Fields Mongo/our own bookkeeping adds that are not part of any entity's
# JSONSchema contract. Several contracts (retailer/product/order/order_created)
# declare `additionalProperties: false`, so these must be stripped before
# validation - they are still preserved in the pre-validation source copy.
_NON_CONTRACT_FIELDS = ("_id", "updated_at")


@dataclass(frozen=True)
class MongoCollectionConfig:
    collection: str
    entity: str
    kind: str  # "batch" | "event"


@dataclass(frozen=True)
class MongoConfig:
    mongo_user: str
    mongo_host: str
    mongo_app_name: str
    mongo_uri_template: str
    database: str
    batch_size: int
    watermark_field: str
    watermark_state_path: Path
    collections: list[MongoCollectionConfig]
    filters: dict[str, dict[str, Any]]


def load_mongo_config(path: Path = MONGO_CONFIG_PATH) -> MongoConfig:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return MongoConfig(
        mongo_user=raw["mongo_user"],
        mongo_host=raw["mongo_host"],
        mongo_app_name=raw["mongo_app_name"],
        mongo_uri_template=raw["mongo_uri_template"],
        database=raw["database"],
        batch_size=int(raw.get("batch_size", 500)),
        watermark_field=raw.get("watermark_field", "updated_at"),
        watermark_state_path=PROJECT_ROOT / raw.get(
            "watermark_state_path", "data/raw/_mongo_watermarks.json"
        ),
        collections=[
            MongoCollectionConfig(
                collection=item["collection"], entity=item["entity"], kind=item["kind"]
            )
            for item in raw.get("collections", [])
        ],
        filters=raw.get("filters") or {},
    )


def build_mongo_uri(config: MongoConfig) -> str:
    """Substitute env vars into mongo_uri_template. MONGO_PASSWORD must be set
    in the environment - it is never read from config or hardcoded."""
    password = os.environ.get("MONGO_PASSWORD")
    if not password:
        raise RuntimeError(
            "MONGO_PASSWORD environment variable is not set. Set it before running "
            "MongoDB ingestion, e.g. `export MONGO_PASSWORD=...` (bash) or "
            "`$env:MONGO_PASSWORD = \"...\"` (PowerShell). The password is never "
            "stored in config/mongo.yaml or committed to the repo."
        )
    template = Template(config.mongo_uri_template)
    return template.safe_substitute(
        MONGO_USER=os.environ.get("MONGO_USER", config.mongo_user),
        MONGO_HOST=os.environ.get("MONGO_HOST", config.mongo_host),
        MONGO_APP_NAME=os.environ.get("MONGO_APP_NAME", config.mongo_app_name),
        MONGO_PASSWORD=password,
    )


def _load_watermarks(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_watermarks(path: Path, watermarks: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(watermarks, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _json_safe(value: Any) -> Any:
    """Recursively coerce a pymongo document (ObjectId, datetime, Decimal128,
    etc.) into plain JSON-serializable Python types."""
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if type(value).__name__ == "ObjectId":
        return str(value)
    if type(value).__name__ == "Decimal128":
        return float(value.to_decimal())
    return value


def raw_table_for(entity: str, kind: str) -> str:
    return f"raw_{entity}" if kind == "batch" else f"raw_{entity}_events"


def fetch_documents(
    collection_cfg: MongoCollectionConfig, config: MongoConfig
) -> list[dict[str, Any]]:
    """Connect to MongoDB and pull up to `batch_size` documents newer than the
    stored watermark for this collection. Requires pymongo + MONGO_PASSWORD."""
    from pymongo import ASCENDING, MongoClient

    uri = build_mongo_uri(config)
    watermarks = _load_watermarks(config.watermark_state_path)
    last_watermark = watermarks.get(collection_cfg.collection)

    query: dict[str, Any] = dict(config.filters.get(collection_cfg.collection, {}))
    if last_watermark is not None:
        query[config.watermark_field] = {"$gt": last_watermark}

    client = MongoClient(uri)
    try:
        cursor = (
            client[config.database][collection_cfg.collection]
            .find(query)
            .sort(config.watermark_field, ASCENDING)
            .limit(config.batch_size)
        )
        return [_json_safe(document) for document in cursor]
    finally:
        client.close()


def _advance_watermark(
    collection_cfg: MongoCollectionConfig, config: MongoConfig, documents: list[dict[str, Any]]
) -> None:
    if not documents:
        return
    values = [document.get(config.watermark_field) for document in documents]
    values = [value for value in values if value is not None]
    if not values:
        return
    watermarks = _load_watermarks(config.watermark_state_path)
    watermarks[collection_cfg.collection] = max(values)
    _save_watermarks(config.watermark_state_path, watermarks)


def mongo_run_id(entity: str, run_started_at: str) -> str:
    compact = run_started_at.replace(":", "").replace("-", "").replace(".", "").replace("+", "z")
    return f"mongo_{entity}_{compact}"


def ingest_mongo_collection(
    collection_cfg: MongoCollectionConfig,
    config: MongoConfig,
    documents: list[dict[str, Any]] | None = None,
) -> IngestionRun:
    """Pull (or accept pre-fetched) documents for one collection, write each as
    its own raw JSON file, validate, quarantine, and emit metadata + lineage -
    mirroring ingest_batch_file / ingest_event_file but per-document rather
    than per-array-file, since a Mongo pull has no single source file."""
    started_at = utc_now()
    entity = collection_cfg.entity
    run_id = mongo_run_id(entity, started_at)

    if documents is None:
        documents = fetch_documents(collection_cfg, config)

    base = RAW_DIR / entity / run_id
    source_dir = base / "source"
    valid_dir = base / "valid"
    quarantine_dir = base / "quarantine"
    metadata_path = base / "metadata" / "ingestion_run.json"

    contractable_records: list[dict[str, Any]] = []
    doc_uuids: list[str] = []
    for document in documents:
        doc_uuid = str(uuid.uuid4())
        doc_uuids.append(doc_uuid)
        write_json(source_dir / f"{doc_uuid}.json", [document])
        contractable_records.append(
            {key: value for key, value in document.items() if key not in _NON_CONTRACT_FIELDS}
        )

    result = validate_records(entity, contractable_records)

    # result.invalid_records already carries the original 0-based index (into
    # contractable_records) as "record_index" - reuse it directly instead of
    # re-deriving membership via dict equality, which would be both slow and
    # unreliable if two pulled documents happen to be byte-identical.
    invalid_by_index = {item["record_index"]: item for item in result.invalid_records}
    for index, record in enumerate(contractable_records):
        doc_uuid = doc_uuids[index]
        if index in invalid_by_index:
            # Wrap in a one-item list so this matches the array-of-wrapped-items
            # shape every other quarantine file uses (see write_valid_and_quarantine
            # + api/metrics_api.py's quarantine_records(), which does
            # `for item in payload: item.get("record_index"...)` and would break
            # on a bare object).
            write_json(quarantine_dir / f"{doc_uuid}.json", [invalid_by_index[index]])
        else:
            write_json(valid_dir / f"{doc_uuid}.json", [record])

    completed_at = utc_now()
    status = "success" if not result.invalid_records else "completed_with_quarantine"
    run = IngestionRun(
        run_id=run_id,
        source="mongo",
        entity=entity,
        file_name=f"mongo://{config.database}.{collection_cfg.collection}",
        source_path=str(source_dir),
        source_content_sha256="",
        partition_path=collection_cfg.collection,
        contract_name=CONTRACT_BY_ENTITY[entity],
        valid_count=len(result.valid_records),
        invalid_count=len(result.invalid_records),
        schema_version="2020-12",
        valid_path=str(valid_dir),
        quarantine_path=str(quarantine_dir),
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
                source_node=f"mongo://{config.database}.{collection_cfg.collection}",
                target_node=str(valid_dir),
                edge_type="validated_to_valid_raw",
                entity=entity,
                created_at=completed_at,
            ),
            LineageEdge(
                run_id=run_id,
                source_node=f"mongo://{config.database}.{collection_cfg.collection}",
                target_node=str(quarantine_dir),
                edge_type="validated_to_quarantine",
                entity=entity,
                created_at=completed_at,
            ),
            LineageEdge(
                run_id=run_id,
                source_node=str(valid_dir),
                target_node=f"raw.{raw_table_for(entity, collection_cfg.kind)}",
                edge_type="loaded_to_raw_table",
                entity=entity,
                created_at=completed_at,
            ),
        ]
    )
    _advance_watermark(collection_cfg, config, documents)
    return run


def ingest_all_mongo_collections(config: MongoConfig | None = None) -> list[IngestionRun]:
    config = config or load_mongo_config()
    runs: list[IngestionRun] = []
    for collection_cfg in config.collections:
        documents = fetch_documents(collection_cfg, config)
        if not documents:
            continue
        runs.append(ingest_mongo_collection(collection_cfg, config, documents=documents))
    return runs


if __name__ == "__main__":
    for ingestion_run in ingest_all_mongo_collections():
        print(ingestion_run)
