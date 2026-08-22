from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_valid_and_quarantine(
    *,
    valid_path: Path,
    quarantine_path: Path,
    valid_records: list[dict[str, Any]],
    invalid_records: list[dict[str, Any]],
) -> None:
    write_json(valid_path, valid_records)
    write_json(quarantine_path, invalid_records)


# ---------------------------------------------------------------------------
# Shared path parsing for quarantine artifacts. Originally lived only in
# api/metrics_api.py's quarantine_records() endpoint; moved here (Phase 5,
# PHASE5-MONITORING.md Section 4) so monitoring/schema_drift.py can reuse the
# exact same logic instead of re-deriving it, and so the fix below applies
# everywhere these paths are parsed.
#
# Bug fix while moving: entity_from_quarantine_path() only recognized the
# `data/raw/batch/<entity>/...` and `data/raw/events/<event_type>/...` zones.
# It silently returned "unknown" for the flatter Mongo-sourced zone
# (`data/raw/<entity>/<run_id>/quarantine/*.json`, written by
# ingestion/mongo_ingest.py - see governance/lineage.md's "MongoDB Ingestion
# Lineage" section), mis-tagging every Mongo-sourced quarantine record in
# both /metadata/quarantine-records and the new schema-drift scanner. The
# `"raw" in parts` branch below covers that zone: RAW_DIR's own name ("raw")
# is always the fixed anchor segment immediately before the zone begins, so
# the segment right after it is the entity name whenever the more specific
# batch/events zones don't match first.
# ---------------------------------------------------------------------------


def run_id_from_quarantine_path(path: Path) -> str:
    """The run_id directory is always the immediate parent of `quarantine/`
    across every zone (batch, events, and the flat Mongo-sourced zone)."""
    try:
        return path.parents[1].name
    except IndexError:
        return "unknown"


def entity_from_quarantine_path(path: Path) -> str:
    parts = path.parts
    if "batch" in parts:
        return parts[parts.index("batch") + 1]
    if "events" in parts:
        return parts[parts.index("events") + 1]
    if "raw" in parts:
        return parts[parts.index("raw") + 1]
    return "unknown"

