"""Continuous synthetic streaming generator (PHASE4-REALTIME&STREAMING.md
Section 1).

Where synthetic/generator.py produces one large batch (retailers, products,
orders, and their full event chain) in a single deterministic pass, this
module runs a long-lived loop that emits individual marketplace EVENTS in
near-real time, at a configurable pace: order_created, order_paid,
orders_shipped, inventory_updated, price_changed. It deliberately never
mints new retailers/products/batch order rows - it streams events against
the existing catalog built by synthetic.id_registry.build_registry(), the
same catalog synthetic/generator.py bootstraps its own runs from.

Every emitted event is, by default, immediately run through the exact same
validate -> quarantine -> metadata -> lineage machinery every other source
uses:

- files sink: written to data/events/<event_type>/YYYY/MM/DD/HH/<uuid>.json
  and, unless --no-local-ingest is passed, passed straight to
  ingestion.event_ingestion.ingest_event_file() (a local DuckDB write). With
  --no-local-ingest, only the file is written; orchestration/realtime_flow.py's
  own file-polling picks it up and does the DuckDB write instead.
- mongo sink (preferred - PHASE4-REALTIME&STREAMING.md's stated preference):
  inserted into the mapped MongoDB collection (config/mongo.yaml) exactly
  like synthetic/write_mongo.py does, then - unless --no-local-ingest is
  passed - also run through ingestion.mongo_ingest.ingest_mongo_collection()
  with the just-inserted document, so metadata/lineage/quarantine appear
  immediately without requiring ingestion/mongo_change_stream.py to also be
  running. mongo_change_stream.py can still independently pick up the same
  insert via its own change-stream watch; duplicate ingestion runs are
  harmless (ingestion_runs keys by run_id, and mart tables delete-insert by
  natural/event key), so running both is fine, not required.

--no-local-ingest is recommended for BOTH sinks whenever stream_generator.py
runs at the same time as orchestration/realtime_flow.py: it keeps
realtime_flow.py as the sole process opening write connections to
mini_faire.duckdb, which is what backs the "safe combination" documented in
README.md's Real-time streaming section (avoids DuckDB single-writer lock
contention between this process and realtime_flow.py).

This module reuses synthetic/generator.py's helpers (timestamp formatting,
schema-driven invalid-record corruption, the ID registry) rather than
re-implementing them, so streamed events stay indistinguishable in shape
from batch-generated ones, and the same deliberate quarantine rate keeps
being exercised in real time.

This module does NOT rebuild the DuckDB warehouse or refresh compute
metrics after each event - at dozens of events/minute that would be wasteful
and is exactly what orchestration/realtime_flow.py (Section 3) exists to do
on a debounced/incremental basis instead. Run stream_generator.py alongside
realtime_flow.py (or periodically re-run scripts/run_demo.py) to see
streamed events reflected in the warehouse/API/frontend.

Run with:
  .\\.venv\\Scripts\\python.exe synthetic\\stream_generator.py
  .\\.venv\\Scripts\\python.exe synthetic\\stream_generator.py --duration-seconds 120
  .\\.venv\\Scripts\\python.exe synthetic\\stream_generator.py --sink files
  .\\.venv\\Scripts\\python.exe synthetic\\stream_generator.py --events-per-minute 60
"""

from __future__ import annotations

import argparse
import os
import random
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from ingestion.heartbeat import write_heartbeat
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DATA_DIR, PROJECT_ROOT
from synthetic.generator import _fmt_ts, make_invalid_variant
from synthetic.id_registry import Registry, build_registry

SYNTHETIC_CONFIG_PATH = PROJECT_ROOT / "config" / "synthetic.yaml"


@dataclass(frozen=True)
class StreamingConfig:
    enabled: bool
    events_per_minute: float
    lifecycle_probabilities: dict[str, float]


def load_config(path: Path = SYNTHETIC_CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def streaming_config_from(full_config: dict) -> StreamingConfig:
    raw = full_config.get("streaming") or {}
    return StreamingConfig(
        enabled=bool(raw.get("enabled", True)),
        events_per_minute=float(raw.get("events_per_minute", 30)),
        lifecycle_probabilities=dict(
            raw.get("lifecycle_probabilities")
            or {"order_paid": 0.95, "orders_shipped": 0.90, "inventory_updated": 0.40, "price_changed": 0.10}
        ),
    )


# ---------------------------------------------------------------------------
# Lifecycle delay compression: generator.py's batch mode writes realistic lags
# straight into event_ts (payment_lag_minutes up to 240, shipping up to
# 1440) because nothing waits on them - the whole dataset is generated in one
# pass. A live stream can't block wall-clock minutes/hours between an
# order_created and its order_paid, so the REAL emission delay is compressed
# into a few seconds while the RECORDED event_ts still reflects the
# realistic lag (order_ts + lag_minutes), keeping downstream event-lag
# metrics meaningful.
# ---------------------------------------------------------------------------


def _compressed_seconds(lag_minutes: float, floor_s: float, ceil_s: float, divisor: float) -> float:
    return max(floor_s, min(ceil_s, lag_minutes / divisor))


@dataclass
class StreamState:
    rng: random.Random
    registry: Registry
    retailer_pool: list[str]
    product_pool: list[str]
    live_price: dict[str, float]
    live_inventory: dict[str, int]
    base_cfg: dict
    streaming_cfg: StreamingConfig
    pending: list[tuple[float, str, dict]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def bump(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1


def _build_state(base_cfg: dict, streaming_cfg: StreamingConfig, seed: int | None) -> StreamState:
    registry = build_registry()
    if not registry.retailers or not registry.products:
        raise SystemExit(
            "No retailer/product catalog found under data/batch/. Run "
            "scripts/run_demo.py or `python -m orchestration.synthetic_flow` at least "
            "once first so stream_generator.py has retailers/products to stream "
            "order/inventory/price events against."
        )
    rng = random.Random(seed) if seed is not None else random.Random()
    return StreamState(
        rng=rng,
        registry=registry,
        retailer_pool=list(registry.retailers.keys()),
        product_pool=list(registry.products.keys()),
        live_price={pid: float(rec.get("unit_price", 0)) for pid, rec in registry.products.items()},
        live_inventory={pid: int(rec.get("inventory_count", 0)) for pid, rec in registry.products.items()},
        base_cfg=base_cfg,
        streaming_cfg=streaming_cfg,
    )


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------


class FileSink:
    """Writes each event to its own JSON file and, by default, ingests it
    immediately (a local DuckDB write). Pass local_ingest=False (the
    --no-local-ingest CLI flag) to only write the file and rely on
    orchestration/realtime_flow.py's own file-polling (_scan_source_signatures
    -> ingest_all_events()) to pick it up and do the DuckDB write instead -
    useful when stream_generator.py and realtime_flow.py run at the same
    time, so only one process is opening write connections to
    mini_faire.duckdb."""

    name = "files"

    def __init__(self, local_ingest: bool = True):
        self.local_ingest = local_ingest

    def emit(self, event_type: str, record: dict) -> None:
        from ingestion.quarantine import write_json

        now = datetime.now(UTC)
        path = (
            DATA_DIR
            / "events"
            / event_type
            / f"{now.year:04d}"
            / f"{now.month:02d}"
            / f"{now.day:02d}"
            / f"{now.hour:02d}"
            / f"{uuid.uuid4()}.json"
        )
        write_json(path, [record])
        if self.local_ingest:
            from ingestion.event_ingestion import ingest_event_file

            run = ingest_event_file(event_type, path)
            _emit_stream_lineage_edge(event_type, f"synthetic://stream_generator/{event_type}", str(path), run.run_id)

    def close(self) -> None:
        pass


class MongoSink:
    """Inserts each event into its mapped MongoDB collection and, by
    default, also runs it through the local per-document ingestion path
    (see module docstring) so metadata/lineage appear without requiring
    ingestion/mongo_change_stream.py to also be running."""

    name = "mongo"

    def __init__(self, local_ingest: bool = True):
        from ingestion.mongo_ingest import build_mongo_uri, load_mongo_config
        from pymongo import MongoClient

        self.local_ingest = local_ingest
        self.config = load_mongo_config()
        self.collection_by_entity = {item.entity: item for item in self.config.collections}
        uri = build_mongo_uri(self.config)
        self.client = MongoClient(uri)
        self.database = self.client[self.config.database]

    def emit(self, event_type: str, record: dict) -> None:
        from ingestion.mongo_ingest import _json_safe, ingest_mongo_collection

        collection_cfg = self.collection_by_entity.get(event_type)
        if collection_cfg is None:
            raise KeyError(
                f"'{event_type}' has no entry under config/mongo.yaml's `collections:` "
                "list, so it cannot be streamed to Mongo. Add one, or run with --sink files."
            )
        document = {**record, "updated_at": datetime.now(UTC).isoformat()}
        self.database[collection_cfg.collection].insert_one(document)
        if self.local_ingest:
            run = ingest_mongo_collection(collection_cfg, self.config, documents=[_json_safe(document)])
            _emit_stream_lineage_edge(
                event_type,
                f"synthetic://stream_generator/{event_type}",
                f"mongo://{self.config.database}.{collection_cfg.collection}",
                run.run_id,
            )

    def close(self) -> None:
        self.client.close()


def _emit_stream_lineage_edge(entity: str, source_node: str, target_node: str, run_id: str) -> None:
    """Extra lineage edge (on top of what ingest_event_file/ingest_mongo_collection
    already emit) explicitly marking this run as streaming-sourced, per
    PHASE4-REALTIME&STREAMING.md Section 4C ("emit lineage edges for streaming
    ingestion")."""
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=run_id,
                source_node=source_node,
                target_node=target_node,
                edge_type="streamed_from_generator",
                entity=entity,
                created_at=utc_now(),
            )
        ]
    )


def _select_sink(name: str, local_ingest: bool) -> FileSink | MongoSink:
    if name == "files":
        return FileSink(local_ingest=local_ingest)
    if name == "mongo":
        return MongoSink(local_ingest=local_ingest)
    # auto: prefer Mongo (per spec) when it looks usable, else fall back to files.
    if os.environ.get("MONGO_PASSWORD"):
        try:
            import pymongo  # noqa: F401

            return MongoSink(local_ingest=local_ingest)
        except ImportError:
            print("pymongo not installed (pip install -e \".[mongo]\") - falling back to --sink files.")
        except Exception as exc:  # connection/auth errors, etc.
            print(f"Could not initialize Mongo sink ({exc!r}) - falling back to --sink files.")
    else:
        print("MONGO_PASSWORD not set - using --sink files. Set it to stream into MongoDB instead.")
    return FileSink(local_ingest=local_ingest)


# ---------------------------------------------------------------------------
# Event builders - mirror synthetic/generator.py's shapes exactly.
# ---------------------------------------------------------------------------


def _maybe_emit_with_corruption(state: StreamState, entity: str, record: dict, emit: Callable[[str, dict], None]) -> None:
    emit(entity, record)
    state.bump(entity)
    if state.rng.random() < state.base_cfg["anomalies"]["invalid_record_rate"]:
        emit(entity, make_invalid_variant(state.rng, entity, record))
        state.bump(f"{entity}_invalid")


def _emit_order_created(state: StreamState, emit: Callable[[str, dict], None]) -> dict:
    retailer_id = state.rng.choice(state.retailer_pool)
    product_id = state.rng.choice(state.product_pool)
    unit_price = state.live_price.get(
        product_id, state.registry.products[product_id]["unit_price"]
    )
    quantity = state.rng.randint(*state.base_cfg["orders"]["quantity_range"])
    gross_amount = round(unit_price * quantity, 2)
    order_ts = datetime.now(UTC)
    order_id = state.registry.next_id("order")
    record = {
        "event_id": state.registry.next_id("event"),
        "event_type": "order_created",
        "event_ts": _fmt_ts(order_ts),
        "order_id": order_id,
        "retailer_id": retailer_id,
        "product_id": product_id,
        "quantity": quantity,
        "gross_amount": gross_amount,
    }
    _maybe_emit_with_corruption(state, "order_created", record, emit)
    return {"order_id": order_id, "order_ts": order_ts, "gross_amount": gross_amount}


def _emit_order_paid(state: StreamState, order_ctx: dict, emit: Callable[[str, dict], None]) -> None:
    discount_rate = state.rng.uniform(*state.base_cfg["orders"]["discount_rate_range"])
    amount = round(order_ctx["gross_amount"] * (1 - discount_rate), 2)
    record = {
        "event_id": state.registry.next_id("event"),
        "event_type": "order_paid",
        "event_ts": _fmt_ts(datetime.now(UTC)),
        "order_id": order_ctx["order_id"],
        "amount": amount,
    }
    _maybe_emit_with_corruption(state, "order_paid", record, emit)


def _emit_orders_shipped(state: StreamState, order_ctx: dict, emit: Callable[[str, dict], None]) -> None:
    record = {
        "event_id": state.registry.next_id("event"),
        "event_type": "orders_shipped",
        "event_ts": _fmt_ts(datetime.now(UTC)),
        "order_id": order_ctx["order_id"],
        "carrier": state.rng.choice(state.base_cfg["events"]["carriers"]),
    }
    _maybe_emit_with_corruption(state, "orders_shipped", record, emit)


def _emit_inventory_updated(state: StreamState, emit: Callable[[str, dict], None]) -> None:
    product_id = state.rng.choice(state.product_pool)
    delta = state.rng.randint(*state.base_cfg["events"]["inventory_delta_range"])
    new_level = max(0, state.live_inventory.get(product_id, 0) + delta)
    state.live_inventory[product_id] = new_level
    record = {
        "event_id": state.registry.next_id("event"),
        "event_type": "inventory_updated",
        "event_ts": _fmt_ts(datetime.now(UTC)),
        "product_id": product_id,
        "delta": delta,
        "inventory_count_after": new_level,
    }
    _maybe_emit_with_corruption(state, "inventory_updated", record, emit)


def _emit_price_changed(state: StreamState, emit: Callable[[str, dict], None]) -> None:
    product_id = state.rng.choice(state.product_pool)
    old_price = state.live_price.get(product_id, state.registry.products[product_id]["unit_price"])
    pct = state.rng.uniform(*state.base_cfg["events"]["price_change_pct_range"])
    new_price = max(1.0, round(old_price * (1 + pct), 2))
    state.live_price[product_id] = new_price
    record = {
        "event_id": state.registry.next_id("event"),
        "event_type": "price_changed",
        "event_ts": _fmt_ts(datetime.now(UTC)),
        "product_id": product_id,
        "old_price": round(old_price, 2),
        "new_price": new_price,
    }
    _maybe_emit_with_corruption(state, "price_changed", record, emit)


def _heartbeat(state: StreamState, emit: Callable[[str, dict], None]) -> None:
    """One pacing tick: always emits a new order_created (chaining into
    order_paid/orders_shipped per lifecycle_probabilities), and independently
    rolls standalone inventory_updated/price_changed events."""
    order_ctx = _emit_order_created(state, emit)
    probs = state.streaming_cfg.lifecycle_probabilities
    if state.rng.random() < probs.get("order_paid", 0.95):
        payment_delay = _compressed_seconds(
            state.rng.uniform(*state.base_cfg["events"]["payment_lag_minutes"]),
            floor_s=3.0, ceil_s=25.0, divisor=6.0,
        )
        state.pending.append((time.monotonic() + payment_delay, "order_paid", order_ctx))

    if state.rng.random() < probs.get("inventory_updated", 0.40):
        _emit_inventory_updated(state, emit)
    if state.rng.random() < probs.get("price_changed", 0.10):
        _emit_price_changed(state, emit)


def _process_pending(state: StreamState, emit: Callable[[str, dict], None]) -> None:
    now = time.monotonic()
    still_pending: list[tuple[float, str, dict]] = []
    for due_at, kind, ctx in state.pending:
        if due_at > now:
            still_pending.append((due_at, kind, ctx))
            continue
        if kind == "order_paid":
            _emit_order_paid(state, ctx, emit)
            probs = state.streaming_cfg.lifecycle_probabilities
            if state.rng.random() < probs.get("orders_shipped", 0.90):
                shipping_delay = _compressed_seconds(
                    state.rng.uniform(*state.base_cfg["events"]["shipping_lag_minutes"]),
                    floor_s=5.0, ceil_s=40.0, divisor=20.0,
                )
                state.pending.append((time.monotonic() + shipping_delay, "orders_shipped", ctx))
        elif kind == "orders_shipped":
            _emit_orders_shipped(state, ctx, emit)
    state.pending = still_pending


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

_SHUTDOWN_REQUESTED = False


def _request_shutdown(signum, frame) -> None:  # noqa: ARG001
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True
    print("\nShutdown requested - finishing the current tick and any in-flight lifecycle events...")


def run_stream(
    *,
    sink_name: str = "auto",
    duration_seconds: float | None = None,
    events_per_minute: float | None = None,
    seed: int | None = None,
    local_ingest: bool = True,
    config_path: Path = SYNTHETIC_CONFIG_PATH,
) -> dict[str, int]:
    full_cfg = load_config(config_path)
    streaming_cfg = streaming_config_from(full_cfg)
    if events_per_minute is not None:
        streaming_cfg = StreamingConfig(
            enabled=streaming_cfg.enabled,
            events_per_minute=events_per_minute,
            lifecycle_probabilities=streaming_cfg.lifecycle_probabilities,
        )
    if not streaming_cfg.enabled:
        print("config/synthetic.yaml streaming.enabled is false - nothing to do.")
        return {}

    state = _build_state(full_cfg, streaming_cfg, seed)
    sink = _select_sink(sink_name, local_ingest)
    print(
        f"Streaming via {sink.name} sink at ~{streaming_cfg.events_per_minute:.1f} "
        f"order_created/min (plus chained + standalone events)."
        + (f" Duration cap: {duration_seconds:.0f}s." if duration_seconds else " Running until interrupted (Ctrl+C).")
    )

    previous_handlers = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[sig] = signal.signal(sig, _request_shutdown)
        except (ValueError, OSError):
            pass  # e.g. not the main thread, or unsupported on this platform

    tick_interval = 60.0 / max(streaming_cfg.events_per_minute, 0.001)
    poll_interval = max(0.25, min(tick_interval, 1.0))
    next_heartbeat_due = time.monotonic()
    start = time.monotonic()
    heartbeats = 0

    try:
        while True:
            if _SHUTDOWN_REQUESTED:
                break
            if duration_seconds is not None and (time.monotonic() - start) >= duration_seconds:
                print(f"Duration cap of {duration_seconds:.0f}s reached.")
                break

            _process_pending(state, sink.emit)

            if time.monotonic() >= next_heartbeat_due:
                _heartbeat(state, sink.emit)
                heartbeats += 1
                next_heartbeat_due += tick_interval
                write_heartbeat(
                    "stream_generator",
                    {"status": "running", "sink": sink.name, "heartbeats": heartbeats, "counts": dict(state.counts)},
                )
                if heartbeats % 10 == 0:
                    print(f"  ...{heartbeats} heartbeats, counts so far: {dict(state.counts)}")

            time.sleep(poll_interval)

        # Drain any still-pending lifecycle events (order_paid/orders_shipped
        # already queued) rather than dropping them on shutdown, capped so a
        # slow drain can't hang the process indefinitely.
        drain_deadline = time.monotonic() + 15.0
        while state.pending and time.monotonic() < drain_deadline:
            _process_pending(state, sink.emit)
            if state.pending:
                time.sleep(poll_interval)
    finally:
        sink.close()
        write_heartbeat(
            "stream_generator",
            {"status": "stopped", "sink": sink.name, "heartbeats": heartbeats, "counts": dict(state.counts)},
        )
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    print(f"Stopped after {heartbeats} heartbeats. Final counts: {dict(state.counts)}")
    return dict(state.counts)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--sink", choices=["auto", "mongo", "files"], default="auto",
        help="auto (default): Mongo if MONGO_PASSWORD is set and pymongo is installed, else files.",
    )
    parser.add_argument("--duration-seconds", type=float, default=None, help="Stop after N seconds (default: run until Ctrl+C).")
    parser.add_argument("--events-per-minute", type=float, default=None, help="Override config/synthetic.yaml's streaming.events_per_minute.")
    parser.add_argument("--seed", type=int, default=None, help="Seed the RNG for a reproducible run (default: time-based/non-deterministic, matching a real live feed).")
    parser.add_argument(
        "--no-local-ingest", action="store_true",
        help=(
            "Skip the immediate local DuckDB ingest for each emitted event and rely on "
            "orchestration/realtime_flow.py (files sink) or ingestion/mongo_change_stream.py "
            "(mongo sink) to pick it up instead. Recommended when running stream_generator.py "
            "at the same time as realtime_flow.py, so only one process writes to mini_faire.duckdb."
        ),
    )
    parser.add_argument("--config", type=Path, default=SYNTHETIC_CONFIG_PATH, help="Path to synthetic.yaml.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    run_stream(
        sink_name=args.sink,
        duration_seconds=args.duration_seconds,
        events_per_minute=args.events_per_minute,
        seed=args.seed,
        local_ingest=not args.no_local_ingest,
        config_path=args.config,
    )


if __name__ == "__main__":
    main()
