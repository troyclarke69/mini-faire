"""Schema Drift Detection (PHASE5-MONITORING.md Section 4).

ingestion/validate.py's JSONSchema validation already sorts every record
into valid/quarantine at ingest time - that machinery is untouched by this
module. What's missing is a dedicated, structured answer to *why* a record
diverged from its contract (missing field, brand-new field, wrong type,
enum/const violation, or a `format: date-time` issue) and a place to see
that trending over time, rather than only a growing folder of raw quarantine
JSON blobs in the frontend's Quarantine view.

Rather than re-instrumenting every ingestion call site (ingestion/
batch_ingestion.py, event_ingestion.py, mongo_ingest.py, mongo_change_stream.py
all already validate and quarantine independently), this module re-validates
already-quarantined records: `scan_quarantine_for_drift()` walks
`data/raw/**/quarantine/*.json` (the same tree api/metrics_api.py's
`/metadata/quarantine-records` reads), re-runs each record through its
entity's JSONSchema validator to recover *why* it failed (jsonschema's
`ValidationError.validator` tells us the failing keyword - "required",
"additionalProperties", "type", "enum", "format", etc. - which the persisted
quarantine JSON does not retain, only a rendered message string), and
classifies each error into one of the five drift types. This keeps every
existing ingestion module untouched while still satisfying "compare incoming
raw JSON fields to schema" for every record that has ever failed validation.

A small state file (data/state/_schema_drift_seen.json, path -> mtime) makes
repeated scans incremental: an unchanged quarantine file is skipped, so
calling this every orchestration/realtime_flow.py cycle (Section 7) doesn't
reprocess the whole quarantine tree each time - the same signature-diff
pattern orchestration/realtime_flow.py itself uses for source files.

Alert volume: a single scan can turn up many drift events at once (this
repo's synthetic data deliberately quarantines ~20% of records, see
config/synthetic.yaml's anomalies.invalid_record_rate) - dispatching one
alert per record would flood every channel. persist_drift_events() instead
dispatches at most one `schema_drift_detected` alert per call, summarizing
counts by drift_type at the highest severity seen, while still persisting
every individual DriftEvent row so the full detail is queryable via
/monitoring/schema-drift.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DUCKDB_PATH, RAW_DIR, STATE_DIR
from ingestion.quarantine import entity_from_quarantine_path, run_id_from_quarantine_path
from ingestion.validate import load_validator

DRIFT_SEEN_STATE_PATH = STATE_DIR / "_schema_drift_seen.json"

# jsonschema ValidationError.validator (the failing keyword) -> one of the
# five drift types PHASE5-MONITORING.md Section 4 asks for. Keywords with no
# clean mapping (e.g. `pattern`, `minimum`) fall back to "other" rather than
# being force-fit into a bucket that would misrepresent them.
DRIFT_TYPE_BY_VALIDATOR: dict[str, str] = {
    "required": "missing_field",
    "additionalProperties": "new_field",
    "type": "type_mismatch",
    "enum": "enum_violation",
    "const": "enum_violation",
    "format": "timestamp_format_issue",
    "pattern": "type_mismatch",
}

SEVERITY_BY_DRIFT_TYPE: dict[str, str] = {
    "missing_field": "critical",
    "type_mismatch": "warning",
    "new_field": "warning",
    "enum_violation": "warning",
    "timestamp_format_issue": "warning",
    "other": "info",
}

_QUOTED = re.compile(r"'([^']+)'")


@dataclass(frozen=True)
class DriftEvent:
    drift_id: str
    entity: str
    drift_type: str
    field_name: str
    expected: str
    actual: str
    severity: str
    detected_at: str
    source_path: str
    run_id: str


def _truncate(value: str, limit: int = 200) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _quoted_names(message: str) -> list[str]:
    """jsonschema's `required`/`additionalProperties` messages are the only
    place field names appear as quoted substrings (e.g. "'x' is a required
    property", "Additional properties are not allowed ('a', 'b' were
    unexpected)") - collecting every quoted token is a robust way to recover
    the field name(s) without depending on singular/plural message wording."""
    return _QUOTED.findall(message) or ["(unknown)"]


def _drift_id(entity: str, field_name: str) -> str:
    compact = utc_now().replace(":", "").replace("-", "").replace(".", "").replace("+", "z")
    safe_field = "".join(ch if ch.isalnum() else "_" for ch in str(field_name))[:40]
    return f"drift_{entity}_{safe_field}_{compact}_{uuid.uuid4().hex[:8]}"


def _build_event(
    entity: str, drift_type: str, *, field_name: str, expected: str, actual: str,
    source_path: str, run_id: str,
) -> DriftEvent:
    return DriftEvent(
        drift_id=_drift_id(entity, field_name),
        entity=entity,
        drift_type=drift_type,
        field_name=field_name,
        expected=_truncate(expected),
        actual=_truncate(actual),
        severity=SEVERITY_BY_DRIFT_TYPE.get(drift_type, "info"),
        detected_at=utc_now(),
        source_path=source_path,
        run_id=run_id,
    )


def _classify_error(entity: str, error, *, source_path: str, run_id: str) -> list[DriftEvent]:
    drift_type = DRIFT_TYPE_BY_VALIDATOR.get(error.validator, "other")

    if error.validator == "required":
        return [
            _build_event(
                entity, drift_type, field_name=name, expected="present", actual="missing",
                source_path=source_path, run_id=run_id,
            )
            for name in _quoted_names(error.message)
        ]
    if error.validator == "additionalProperties":
        return [
            _build_event(
                entity, drift_type, field_name=name, expected="not declared in contract", actual="present",
                source_path=source_path, run_id=run_id,
            )
            for name in _quoted_names(error.message)
        ]

    field_name = ".".join(str(part) for part in error.path) or "(root)"
    return [
        _build_event(
            entity, drift_type, field_name=field_name,
            expected=str(error.validator_value), actual=str(error.instance),
            source_path=source_path, run_id=run_id,
        )
    ]


def analyze_records(entity: str, records: list[dict[str, Any]], *, source_path: str = "", run_id: str = "") -> list[DriftEvent]:
    """Pure function: validate `records` against `entity`'s JSONSchema
    contract and classify every resulting error. No I/O beyond loading the
    schema file itself - safe to call directly (e.g. from tests, or a future
    caller that wants to check records before they're even written to disk)."""
    validator = load_validator(entity)
    events: list[DriftEvent] = []
    for record in records:
        for error in validator.iter_errors(record):
            events.extend(_classify_error(entity, error, source_path=source_path, run_id=run_id))
    return events


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def scan_quarantine_for_drift(seen_state_path: Path = DRIFT_SEEN_STATE_PATH) -> list[DriftEvent]:
    """Walk every quarantine artifact under data/raw/, skip ones already
    processed at their current mtime, and classify the rest. Updates the seen
    state as a side effect - call this once per orchestration cycle, not per
    request (api/monitoring_api.py reads the persisted table instead, it
    never calls this directly)."""
    seen = _load_state(seen_state_path)
    current: dict[str, float] = {}
    events: list[DriftEvent] = []

    for path in RAW_DIR.glob("**/quarantine/*.json"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        current[str(path)] = mtime
        if seen.get(str(path)) == mtime:
            continue  # unchanged since the last scan

        entity = entity_from_quarantine_path(path)
        run_id = run_id_from_quarantine_path(path)
        if entity == "unknown":
            continue  # can't select a contract without knowing the entity

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        for item in payload:
            record = item.get("record")
            if not isinstance(record, dict):
                continue
            try:
                events.extend(analyze_records(entity, [record], source_path=str(path), run_id=run_id))
            except KeyError:
                continue  # entity has no known contract (shouldn't happen, but never let one file break the scan)

    _save_state(seen_state_path, current)
    return events


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists monitoring")
    con.execute(
        """
        create table if not exists monitoring.schema_drift_events (
          drift_id varchar primary key,
          entity varchar,
          drift_type varchar,
          field_name varchar,
          expected varchar,
          actual varchar,
          severity varchar,
          detected_at timestamptz,
          source_path varchar,
          run_id varchar
        )
        """
    )


def persist_drift_events(events: list[DriftEvent], db_path: Path = DUCKDB_PATH, *, dispatch: bool = True) -> list[DriftEvent]:
    if not events:
        return events

    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.executemany(
            """
            insert or replace into monitoring.schema_drift_events
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    e.drift_id, e.entity, e.drift_type, e.field_name, e.expected,
                    e.actual, e.severity, e.detected_at, e.source_path, e.run_id,
                )
                for e in events
            ],
        )
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=e.run_id or e.drift_id,
                source_node=e.source_path or f"schema_drift://{e.entity}",
                target_node="monitoring.schema_drift_events",
                edge_type="schema_drift_detected",
                entity=e.entity,
                created_at=e.detected_at,
            )
            for e in events
        ]
    )

    if dispatch:
        _dispatch_summary_alert(events)

    return events


_SEVERITY_ORDER = ("info", "warning", "critical")


def _dispatch_summary_alert(events: list[DriftEvent]) -> None:
    """One alert per scan, not one per record (see module docstring) -
    summarizes counts by drift_type at the highest severity observed."""
    from alerts.dispatcher import dispatch_alert

    by_type: dict[str, int] = {}
    for event in events:
        by_type[event.drift_type] = by_type.get(event.drift_type, 0) + 1
    highest_severity = max((e.severity for e in events), key=lambda s: _SEVERITY_ORDER.index(s) if s in _SEVERITY_ORDER else 0)
    entities = sorted({e.entity for e in events})
    summary = ", ".join(f"{count} {drift_type}" for drift_type, count in sorted(by_type.items()))

    try:
        dispatch_alert(
            "schema_drift_detected",
            entity=", ".join(entities[:5]) + ("..." if len(entities) > 5 else ""),
            message=f"{len(events)} schema drift event(s) found across {len(entities)} entit{'y' if len(entities)==1 else 'ies'}: {summary}",
            metadata={"count": len(events), "by_drift_type": by_type, "entities": entities},
            severity=highest_severity,
        )
    except Exception as exc:  # noqa: BLE001 - alerting must never break the scan
        print(f"  could not dispatch schema_drift_detected alert: {exc!r}")


if __name__ == "__main__":
    found = scan_quarantine_for_drift()
    persist_drift_events(found)
    print(f"Schema drift events: {len(found)}")
    for event in found[:20]:
        print(f"  [{event.severity}] {event.entity} {event.drift_type} field={event.field_name}")
