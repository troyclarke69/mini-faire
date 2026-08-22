"""Optional synthetic -> MongoDB writer (PHASE3-DATA-LOAD.md Section 3E).

Takes a GeneratedDataset (see synthetic/generator.py) and inserts its records
directly into the MongoDB collections configured in config/mongo.yaml,
instead of (or in addition to) writing them to the local data/batch and
data/events source zones via synthetic/write_raw.py. This lets
ingestion/mongo_ingest.py (a poll-based pull) or
ingestion/mongo_ingest_change_stream.py (a live change-stream watch) pick
them up exactly like any other upstream document, enabling an end-to-end
"synthetic data appears in Mongo -> gets pulled/streamed -> flows through
validation/quarantine/ELT/compute" demo of a realistic real-time pipeline.

Only entities in the Section 2C collection mapping are Mongo-eligible
(retailers, products, orders, and the order/inventory event chain).
price_changed gained a Mongo collection mapping in Phase 4
(PHASE4-REALTIME&STREAMING.md Section 1/2, config/mongo.yaml) so the
streaming generator can emit every event type through Mongo; before that it
only ever reached the warehouse via synthetic/write_raw.py's local file path.

Every inserted document gets an `updated_at` timestamp (the configured
watermark field), so a subsequent mongo_ingest run picks it up incrementally.

Requires the optional `mongo` dependency group and a MONGO_PASSWORD
environment variable - see config/mongo.yaml. Never hardcode the password.

Run with:
  .\\.venv\\Scripts\\python.exe -m synthetic.write_mongo
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ingestion.mongo_ingest import MongoConfig, build_mongo_uri, load_mongo_config
from synthetic.generator import GeneratedDataset

# Section 2C's MongoDB collection mapping covers these entities/event types
# (price_changed added in Phase 4 - see config/mongo.yaml).
MONGO_ELIGIBLE_ENTITIES = (
    "retailers",
    "products",
    "orders",
    "order_created",
    "order_paid",
    "orders_shipped",
    "inventory_updated",
    "price_changed",
)


def _stamped(record: dict[str, Any]) -> dict[str, Any]:
    return {**record, "updated_at": datetime.now(UTC).isoformat()}


def _batch_documents(dataset: GeneratedDataset) -> dict[str, list[dict[str, Any]]]:
    return {
        "retailers": [_stamped(r) for records in dataset.retailers_by_day.values() for r in records],
        "products": [_stamped(r) for records in dataset.products_by_day.values() for r in records],
        "orders": [_stamped(r) for records in dataset.orders_by_day.values() for r in records],
    }


def _event_documents(dataset: GeneratedDataset) -> dict[str, list[dict[str, Any]]]:
    documents: dict[str, list[dict[str, Any]]] = {}
    for event_type, by_day in dataset.events.items():
        if event_type not in MONGO_ELIGIBLE_ENTITIES:
            continue
        documents[event_type] = [
            _stamped(record)
            for by_hour in by_day.values()
            for records in by_hour.values()
            for record in records
        ]
    return documents


def write_dataset_to_mongo(
    dataset: GeneratedDataset, config: MongoConfig | None = None
) -> dict[str, int]:
    """Insert every Mongo-eligible record from `dataset` into its mapped
    collection. Returns {collection: inserted_count}."""
    from pymongo import MongoClient

    config = config or load_mongo_config()
    uri = build_mongo_uri(config)
    collection_by_entity = {item.entity: item.collection for item in config.collections}

    documents_by_entity = {**_batch_documents(dataset), **_event_documents(dataset)}

    client = MongoClient(uri)
    inserted: dict[str, int] = {}
    try:
        database = client[config.database]
        for entity, records in documents_by_entity.items():
            if entity not in MONGO_ELIGIBLE_ENTITIES or not records:
                continue
            collection_name = collection_by_entity.get(entity, entity)
            result = database[collection_name].insert_many(records)
            inserted[collection_name] = len(result.inserted_ids)
    finally:
        client.close()

    return inserted


if __name__ == "__main__":
    import yaml

    from ingestion.paths import PROJECT_ROOT
    from synthetic.generator import generate_dataset

    with (PROJECT_ROOT / "config" / "synthetic.yaml").open(encoding="utf-8") as handle:
        synthetic_config = yaml.safe_load(handle)

    generated = generate_dataset(synthetic_config)
    counts = write_dataset_to_mongo(generated)
    print(f"Inserted into MongoDB: {counts}")
