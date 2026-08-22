"""Optional real-time MongoDB ingestion via change streams
(PHASE3-DATA-LOAD.md Section 2E).

Where ingestion/mongo_ingest.py does a point-in-time pull (poll on a
schedule, advance a watermark), this module instead opens a MongoDB change
stream on the collections listed in config/mongo.yaml's
`change_stream.enabled_collections` and treats every insert/update as a
live event: as soon as a document lands in one of those collections, its
full document is written to raw JSON, validated, quarantined if invalid,
and given the same metadata + lineage treatment as every other ingestion
source - simulating a real-time event feed on top of a document database
that has no native event log of its own.

Requires MongoDB to be running as a replica set (Atlas clusters are, by
default) - change streams are not available against a standalone mongod.

Run with:
  .\\.venv\\Scripts\\python.exe -m ingestion.mongo_ingest_change_stream
"""

from __future__ import annotations

import time
from typing import Any

from ingestion.metadata import IngestionRun
from ingestion.mongo_ingest import (
    MongoCollectionConfig,
    MongoConfig,
    _json_safe,
    build_mongo_uri,
    ingest_mongo_collection,
    load_mongo_config,
)


def _collection_config_for(config: MongoConfig, collection_name: str) -> MongoCollectionConfig:
    for collection_cfg in config.collections:
        if collection_cfg.collection == collection_name:
            return collection_cfg
    raise KeyError(
        f"'{collection_name}' is listed under change_stream.enabled_collections in "
        "config/mongo.yaml but has no matching entry under `collections:` - add one "
        "so it can be mapped to an entity."
    )


def watch_change_streams(
    config: MongoConfig | None = None, max_seconds: float | None = None
) -> list[IngestionRun]:
    """Open a change stream per enabled collection and ingest each
    insert/update's full document as soon as it arrives, for up to
    `max_seconds` (falls back to config/mongo.yaml's change_stream.max_seconds,
    which defaults to a short window since this is a demo, not a long-running
    daemon)."""
    from pymongo import MongoClient

    config = config or load_mongo_config()
    max_seconds = max_seconds if max_seconds is not None else 60
    uri = build_mongo_uri(config)
    client = MongoClient(uri)
    runs: list[IngestionRun] = []
    deadline = time.monotonic() + max_seconds

    try:
        database = client[config.database]
        watched_names = _enabled_collection_names(config)
        streams = [
            (name, database[name].watch(full_document="updateLookup")) for name in watched_names
        ]
        try:
            while time.monotonic() < deadline:
                progressed = False
                for name, stream in streams:
                    change = stream.try_next()
                    if change is None:
                        continue
                    progressed = True
                    document: dict[str, Any] | None = change.get("fullDocument")
                    if not document:
                        continue
                    collection_cfg = _collection_config_for(config, name)
                    runs.append(
                        ingest_mongo_collection(
                            collection_cfg, config, documents=[_json_safe(document)]
                        )
                    )
                if not progressed:
                    time.sleep(0.5)
        finally:
            for _, stream in streams:
                stream.close()
    finally:
        client.close()

    return runs


def _enabled_collection_names(config: MongoConfig) -> list[str]:
    import yaml

    from ingestion.mongo_ingest import MONGO_CONFIG_PATH

    with open(MONGO_CONFIG_PATH, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return list((raw.get("change_stream") or {}).get("enabled_collections", []))


if __name__ == "__main__":
    for ingestion_run in watch_change_streams():
        print(ingestion_run)
