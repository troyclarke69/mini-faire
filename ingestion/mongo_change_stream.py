"""Real-time MongoDB change-stream ingestion (PHASE4-REALTIME&STREAMING.md
Section 2).

This is a more advanced, standalone sibling of Phase 3's optional
ingestion/mongo_ingest_change_stream.py - both remain usable independently
(see config/mongo.yaml's `change_stream:` vs `change_streams:` blocks). Where
the Phase 3 module opens a fixed short-lived watch per demo run with no
resume-token persistence, this module is built to run as a long-lived
streaming service:

- persists a resume token per collection (config/mongo.yaml's
  change_streams.resume_token_state_path) so a restart resumes watching from
  where it left off instead of re-processing or silently missing events;
- backs off and retries per collection on stream errors (network blips,
  invalidated resume tokens) instead of crashing the whole watcher;
- handles insert/update/replace (full document available - "convert change
  events into raw JSON" + "trigger ingestion pipeline" is satisfied by
  reusing ingestion.mongo_ingest.ingest_mongo_collection(), which already
  writes the raw JSON, validates, quarantines, and emits metadata + lineage
  in one call - see docstring note below on why this reuses that path rather
  than writing a second, parallel raw-JSON layout);
- handles delete (no full document exists to validate - see _handle_delete);
- exposes poll_once()/ChangeStreamWatcher so orchestration/realtime_flow.py
  (Section 3) can drive this from its own event loop instead of only being
  runnable as a standalone process.

PHASE4-REALTIME&STREAMING.md's raw JSON path example is
`data/raw/<entity>/<run_id>/<uuid>.json` (no `source/valid/quarantine`
subfolders). This module deliberately keeps ingest_mongo_collection()'s
existing `<run_id>/source|valid|quarantine|metadata/<uuid>.json` layout
instead, because ingestion/load_duckdb.py's RAW_TABLE_SOURCES only globs
`**/valid/*.json` - writing a flat, unvalidated copy at the literal path the
spec shows would never be picked up by the warehouse loader and would just
be a second, redundant raw copy. Keeping one raw-zone convention across
every ingestion source (batch, event, mongo poll, mongo change-stream) is
more correct than matching the spec's illustrative path byte-for-byte.

Requires the optional `mongo` dependency group (`pip install -e ".[mongo]"`)
and a MONGO_PASSWORD environment variable - see config/mongo.yaml. Never
hardcode the password.

Run with:
  .\\.venv\\Scripts\\python.exe -m ingestion.mongo_change_stream
  .\\.venv\\Scripts\\python.exe -m ingestion.mongo_change_stream --max-seconds 120
"""

from __future__ import annotations

import argparse
import json
import signal
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ingestion.heartbeat import write_heartbeat
from ingestion.metadata import IngestionRun, LineageEdge, upsert_lineage_edges, utc_now
from ingestion.mongo_ingest import (
    MONGO_CONFIG_PATH,
    MongoCollectionConfig,
    MongoConfig,
    _json_safe,
    build_mongo_uri,
    ingest_mongo_collection,
    load_mongo_config,
)
from ingestion.paths import RAW_DIR


@dataclass(frozen=True)
class ChangeStreamsConfig:
    enabled: bool
    collections: list[str]
    resume_token_state_path: Path
    backoff_initial_seconds: float
    backoff_max_seconds: float
    backoff_multiplier: float


def load_change_streams_config(path: Path = MONGO_CONFIG_PATH) -> ChangeStreamsConfig:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    block = raw.get("change_streams") or {}
    from ingestion.paths import PROJECT_ROOT

    return ChangeStreamsConfig(
        enabled=bool(block.get("enabled", True)),
        collections=list(block.get("collections", [])),
        resume_token_state_path=PROJECT_ROOT
        / block.get("resume_token_state_path", "data/raw/_mongo_change_stream_resume_tokens.json"),
        backoff_initial_seconds=float(block.get("backoff_initial_seconds", 1.0)),
        backoff_max_seconds=float(block.get("backoff_max_seconds", 30.0)),
        backoff_multiplier=float(block.get("backoff_multiplier", 2.0)),
    )


def _load_resume_tokens(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_resume_tokens(path: Path, tokens: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _collection_config_for(mongo_config: MongoConfig, collection_name: str) -> MongoCollectionConfig:
    for item in mongo_config.collections:
        if item.collection == collection_name:
            return item
    raise KeyError(
        f"'{collection_name}' is listed under config/mongo.yaml's change_streams.collections "
        "but has no matching entry under `collections:` - add one so it can be mapped to an entity."
    )


@dataclass
class _StreamHandle:
    name: str
    entity: str
    stream: Any = None
    backoff_delay: float = 0.0
    next_attempt_at: float = 0.0  # time.monotonic() timestamp; 0 = ready now


def _delete_run_id(entity: str) -> str:
    compact = utc_now().replace(":", "").replace("-", "").replace(".", "").replace("+", "z")
    return f"delete_{entity}_{compact}_{uuid.uuid4().hex[:8]}"


def _handle_delete(entity: str, collection_name: str, database_name: str, change: dict[str, Any]) -> IngestionRun | None:
    """Deletes carry no fullDocument, so there is nothing to validate against
    an entity contract. Still write an audit artifact + lineage edge so the
    delete is observable (Section 2's "handle deletes" requirement) instead
    of silently dropping it."""
    run_id = _delete_run_id(entity)
    path = RAW_DIR / entity / run_id / "deletes" / f"{uuid.uuid4()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_safe(
        {
            "operation_type": change.get("operationType"),
            "document_key": change.get("documentKey"),
            "cluster_time": change.get("clusterTime"),
            "ns": change.get("ns"),
        }
    )
    path.write_text(json.dumps([payload], indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=run_id,
                source_node=f"mongo://{database_name}.{collection_name}",
                target_node=str(path),
                edge_type="change_stream_delete_observed",
                entity=entity,
                created_at=utc_now(),
            )
        ]
    )
    print(f"  [{collection_name}] delete observed for {change.get('documentKey')} -> {path}")
    return None


class ChangeStreamWatcher:
    """Owns one MongoClient plus one open change-stream per configured
    collection, with resume-token persistence and per-collection backoff."""

    def __init__(self, config: MongoConfig | None = None, streams_config: ChangeStreamsConfig | None = None):
        from pymongo import MongoClient

        self.config = config or load_mongo_config()
        self.streams_config = streams_config or load_change_streams_config()
        self.resume_tokens = _load_resume_tokens(self.streams_config.resume_token_state_path)
        uri = build_mongo_uri(self.config)
        self.client = MongoClient(uri)
        self.database = self.client[self.config.database]
        self.handles: dict[str, _StreamHandle] = {}
        for name in self.streams_config.collections:
            collection_cfg = _collection_config_for(self.config, name)
            self.handles[name] = _StreamHandle(name=name, entity=collection_cfg.entity)

    def _open(self, handle: _StreamHandle) -> None:
        token = self.resume_tokens.get(handle.name)
        kwargs: dict[str, Any] = {"full_document": "updateLookup"}
        if token:
            kwargs["resume_after"] = token
        try:
            handle.stream = self.database[handle.name].watch(**kwargs)
            handle.backoff_delay = 0.0
            handle.next_attempt_at = 0.0
        except Exception as exc:  # noqa: BLE001 - pymongo.errors.PyMongoError and friends
            if token:
                print(
                    f"  [{handle.name}] resume token rejected ({exc!r}) - clearing it and "
                    "will retry from the current point in time."
                )
                self.resume_tokens.pop(handle.name, None)
                _save_resume_tokens(self.streams_config.resume_token_state_path, self.resume_tokens)
            self._backoff(handle, exc)

    def _backoff(self, handle: _StreamHandle, exc: Exception) -> None:
        handle.stream = None
        handle.backoff_delay = (
            self.streams_config.backoff_initial_seconds
            if handle.backoff_delay <= 0
            else min(handle.backoff_delay * self.streams_config.backoff_multiplier, self.streams_config.backoff_max_seconds)
        )
        handle.next_attempt_at = time.monotonic() + handle.backoff_delay
        print(f"  [{handle.name}] stream error ({exc!r}) - retrying in {handle.backoff_delay:.1f}s")

    def poll_once(self) -> list[IngestionRun]:
        """One non-blocking sweep across every configured collection's
        stream: opens any stream that's due for a (re)connect attempt, then
        drains whatever change events are currently available."""
        runs: list[IngestionRun] = []
        for handle in self.handles.values():
            if handle.stream is None:
                if time.monotonic() < handle.next_attempt_at:
                    continue
                self._open(handle)
                if handle.stream is None:
                    continue

            try:
                change = handle.stream.try_next()
            except Exception as exc:  # noqa: BLE001
                self._backoff(handle, exc)
                continue

            if change is None:
                # Persist the resume token even on an idle poll - pymongo
                # advances it internally (postBatchResumeToken) as long as
                # we keep iterating, so this keeps a restart's gap small.
                self.resume_tokens[handle.name] = handle.stream.resume_token
                continue

            op = change.get("operationType")
            if op in ("insert", "update", "replace"):
                document = change.get("fullDocument")
                if document:
                    collection_cfg = _collection_config_for(self.config, handle.name)
                    run = ingest_mongo_collection(collection_cfg, self.config, documents=[_json_safe(document)])
                    upsert_lineage_edges(
                        [
                            LineageEdge(
                                run_id=run.run_id,
                                source_node=f"mongo://{self.config.database}.{handle.name}#change_stream",
                                target_node=run.valid_path,
                                edge_type="change_stream_ingested",
                                entity=handle.entity,
                                created_at=utc_now(),
                            )
                        ]
                    )
                    runs.append(run)
            elif op == "delete":
                _handle_delete(handle.entity, handle.name, self.config.database, change)
            # invalidate/drop/rename/dropDatabase: nothing document-shaped to ingest;
            # fall through and just persist the resume token below.

            self.resume_tokens[handle.name] = handle.stream.resume_token
            _save_resume_tokens(self.streams_config.resume_token_state_path, self.resume_tokens)

        return runs

    def close(self) -> None:
        for handle in self.handles.values():
            if handle.stream is not None:
                try:
                    handle.stream.close()
                except Exception:  # noqa: BLE001
                    pass
        _save_resume_tokens(self.streams_config.resume_token_state_path, self.resume_tokens)
        self.client.close()

    def run_forever(self, max_seconds: float | None = None, poll_interval: float = 0.5) -> list[IngestionRun]:
        global _SHUTDOWN_REQUESTED
        _SHUTDOWN_REQUESTED = False
        previous_handlers = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handlers[sig] = signal.signal(sig, _request_shutdown)
            except (ValueError, OSError):
                pass

        # Startup confirmation: silence during idle polling (no changes yet)
        # is normal and expected, but with zero output at all a standalone run
        # is indistinguishable from one that's hung or misconfigured. Print
        # what's actually being watched once, up front.
        if self.handles:
            print(
                f"Watching {len(self.handles)} MongoDB collection(s) for changes: "
                f"{', '.join(self.handles.keys())} (database={self.config.database}). "
                "No output means no changes yet - that's normal; inserts/updates/deletes "
                "print as they're observed."
            )
        else:
            print(
                "config/mongo.yaml change_streams.collections is empty - there is nothing "
                "to watch. Add collection names there, or this process will idle forever "
                "with no output."
            )

        all_runs: list[IngestionRun] = []
        start = time.monotonic()
        last_heartbeat_write = 0.0
        last_status_print = time.monotonic()
        last_status_runs = 0
        try:
            while True:
                if _SHUTDOWN_REQUESTED:
                    print("Shutdown requested - closing change streams...")
                    break
                if max_seconds is not None and (time.monotonic() - start) >= max_seconds:
                    print(f"Duration cap of {max_seconds:.0f}s reached.")
                    break
                all_runs.extend(self.poll_once())
                now = time.monotonic()
                if now - last_heartbeat_write >= 2.0:
                    write_heartbeat(
                        "mongo_change_stream",
                        {
                            "status": "running",
                            "watched_collections": list(self.handles.keys()),
                            "ingestion_runs_total": len(all_runs),
                        },
                    )
                    last_heartbeat_write = now
                # Periodic idle status line so a long silent stretch reads as
                # "still watching, nothing changed" rather than "did this die?"
                if self.handles and now - last_status_print >= 30.0:
                    new_since_last = len(all_runs) - last_status_runs
                    print(
                        f"  ...still watching {', '.join(self.handles.keys())} "
                        f"({new_since_last} change(s) in the last 30s, {len(all_runs)} total)."
                    )
                    last_status_print = now
                    last_status_runs = len(all_runs)
                time.sleep(poll_interval)
        finally:
            write_heartbeat(
                "mongo_change_stream",
                {"status": "stopped", "watched_collections": list(self.handles.keys()), "ingestion_runs_total": len(all_runs)},
            )
            self.close()
            for sig, handler in previous_handlers.items():
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError):
                    pass
        return all_runs


_SHUTDOWN_REQUESTED = False


def _request_shutdown(signum, frame) -> None:  # noqa: ARG001
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True


def watch_change_streams(
    config: MongoConfig | None = None,
    streams_config: ChangeStreamsConfig | None = None,
    max_seconds: float | None = 60,
    poll_interval: float = 0.5,
) -> list[IngestionRun]:
    """Convenience one-shot entry point (used by orchestration/realtime_flow.py
    and __main__): open a watcher, run it for up to max_seconds (None = until
    interrupted), and return every IngestionRun produced."""
    streams_config = streams_config or load_change_streams_config()
    if not streams_config.enabled:
        print("config/mongo.yaml change_streams.enabled is false - nothing to do.")
        return []
    watcher = ChangeStreamWatcher(config=config, streams_config=streams_config)
    return watcher.run_forever(max_seconds=max_seconds, poll_interval=poll_interval)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch MongoDB change streams and ingest events in real time.")
    parser.add_argument("--max-seconds", type=float, default=None, help="Stop after N seconds (default: run until Ctrl+C).")
    parser.add_argument("--poll-interval", type=float, default=0.5, help="Seconds between non-blocking sweeps of all streams.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    runs = watch_change_streams(max_seconds=args.max_seconds, poll_interval=args.poll_interval)
    print(f"Change-stream ingestion runs: {len(runs)}")
    for run in runs:
        print(f"  {run.entity}: valid={run.valid_count} invalid={run.invalid_count} status={run.status}")


if __name__ == "__main__":
    main()
