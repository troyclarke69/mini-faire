"""Real-time orchestration layer (PHASE4-REALTIME&STREAMING.md Section 3).

Where orchestration/synthetic_flow.py and orchestration/mongo_flow.py each
run one generate/pull -> ingest -> rebuild -> compute pass and exit, this
module is a long-lived coordinator that reacts to new work showing up from
either source:

- new raw JSON files dropped under data/batch/**/*.json or
  data/events/**/*.json (by a human, an external system, or
  synthetic/stream_generator.py's --sink files mode);
- new MongoDB change-stream events (delegated to
  ingestion.mongo_change_stream.ChangeStreamWatcher.poll_once() - this is
  optional and skipped gracefully if pymongo/MONGO_PASSWORD aren't
  available).

New source files are ingested as soon as they're detected (validate ->
quarantine -> metadata -> lineage, same as every other source). Warehouse
staging + incremental ELT + Polars compute are then refreshed on a debounced
cadence rather than on every single new file/event, so a burst of activity
collapses into one rebuild instead of one per file - this is the
"concurrency limits" / "backpressure handling" requirement: at most one
rebuild+compute cycle runs at a time (guarded by a non-blocking lock), and
new work that arrives mid-cycle just waits for the next debounce window
instead of queueing up redundant rebuilds.

"Incremental ELT" here means the same thing it means throughout this repo
(see README's Incremental ELT section and warehouse/duckdb/models/*.sql's
comments): staging is fully rebuilt from the validated raw zone every cycle
for correctness (DuckDB's read_json_auto glob has no cheap "only new files"
mode without extra bookkeeping this demo doesn't need), while mart tables
use natural-key/event-id delete-insert with a high-watermark
(`OR NOT EXISTS`-guarded, see fact_orders.sql) so repeated/out-of-order
runs stay idempotent and cheap in practice. "Incremental" is about running
this refresh only when there's new work and about resuming/coalescing
cleanly, not about a partial/append-only staging load.

PHASE5-MONITORING.md Section 7: after a successful ingest/ELT/compute cycle,
`_run_rebuild_and_compute()` also runs monitoring/metrics.py's reliability
metrics, monitoring/schema_drift.py's quarantine scan, and
anomalies/detector.py's anomaly sweep (`_run_monitoring_pass()` below) - and
if any of the three pipeline stages themselves raise, dispatches the
matching ingestion_failure/elt_failure/compute_failure alert via
alerts/dispatcher.py instead of letting the exception crash this long-lived
process. See `_run_rebuild_and_compute()`'s docstring for why that changed
this method's failure handling from "let it crash" to "catch, alert, retry
next cycle".

Run with:
  .\\.venv\\Scripts\\python.exe -m orchestration.realtime_flow
  .\\.venv\\Scripts\\python.exe -m orchestration.realtime_flow --duration-seconds 120
  .\\.venv\\Scripts\\python.exe -m orchestration.realtime_flow --no-mongo
"""

from __future__ import annotations

import argparse
import glob as glob_module
import os
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ingestion.heartbeat import write_heartbeat
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DATA_DIR

# A burst larger than this in one poll just gets processed anyway, but logged
# loudly rather than silently absorbed, so an operator can see it happened.
_BURST_LOG_THRESHOLD = 200


def _scan_source_signatures() -> dict[str, float]:
    """(path -> mtime) for every batch/event source JSON file currently on
    disk. Cheap enough to re-glob every poll at this repo's demo scale."""
    signatures: dict[str, float] = {}
    for pattern in (str(DATA_DIR / "batch" / "**" / "*.json"), str(DATA_DIR / "events" / "**" / "*.json")):
        for path in glob_module.glob(pattern, recursive=True):
            try:
                signatures[path] = os.path.getmtime(path)
            except OSError:
                continue
    return signatures


@dataclass
class RealtimeFlowStatus:
    cycles_run: int = 0
    rebuilds_run: int = 0
    last_cycle_at: str | None = None
    last_rebuild_at: str | None = None
    last_new_file_count: int = 0
    last_mongo_run_count: int = 0
    mongo_enabled: bool = False


class RealtimeFlow:
    """Owns the file-signature cursor, the optional Mongo change-stream
    watcher, and the debounce/concurrency-limited rebuild trigger."""

    def __init__(self, debounce_seconds: float = 5.0, mongo_enabled: bool = True):
        self.debounce_seconds = debounce_seconds
        self._seen: dict[str, float] = _scan_source_signatures()  # seed from current state; don't rebuild on startup
        self._pending_since: float | None = None
        self._rebuild_lock = threading.Lock()
        self.status = RealtimeFlowStatus()
        self.watcher = None
        if mongo_enabled:
            self._try_open_mongo_watcher()

    def _try_open_mongo_watcher(self) -> None:
        if not os.environ.get("MONGO_PASSWORD"):
            print("MONGO_PASSWORD not set - skipping MongoDB change-stream detection (file-based detection still active).")
            return
        try:
            from ingestion.mongo_change_stream import ChangeStreamWatcher, load_change_streams_config

            streams_cfg = load_change_streams_config()
            if not streams_cfg.enabled:
                print("config/mongo.yaml change_streams.enabled is false - skipping MongoDB change-stream detection.")
                return
            self.watcher = ChangeStreamWatcher(streams_config=streams_cfg)
            self.status.mongo_enabled = True
            print(f"Watching MongoDB change streams: {streams_cfg.collections}")
        except ImportError:
            print("pymongo not installed (pip install -e \".[mongo]\") - skipping MongoDB change-stream detection.")
        except Exception as exc:  # noqa: BLE001
            print(f"Could not open MongoDB change-stream watcher ({exc!r}) - continuing with file-based detection only.")

    def _detect_new_files(self) -> list[str]:
        current = _scan_source_signatures()
        new_or_changed = [
            path for path, mtime in current.items() if self._seen.get(path) != mtime
        ]
        self._seen = current
        if len(new_or_changed) > _BURST_LOG_THRESHOLD:
            print(
                f"  detected a burst of {len(new_or_changed)} new/changed source files "
                f"(>{_BURST_LOG_THRESHOLD}) - processing all of them, but that's unusually large."
            )
        return new_or_changed

    def poll_once(self) -> bool:
        """One detection sweep. Returns True if new work was found (which
        may or may not have triggered a rebuild yet, depending on debounce)."""
        new_files = self._detect_new_files()
        mongo_runs = self.watcher.poll_once() if self.watcher is not None else []

        self.status.cycles_run += 1
        self.status.last_cycle_at = utc_now()
        self.status.last_new_file_count = len(new_files)
        self.status.last_mongo_run_count = len(mongo_runs)

        found_work = bool(new_files) or bool(mongo_runs)
        if found_work:
            if self._pending_since is None:
                self._pending_since = time.monotonic()
            if new_files:
                print(f"  detected {len(new_files)} new/changed source file(s)")
            if mongo_runs:
                print(f"  detected {len(mongo_runs)} new MongoDB change-stream document(s)")

        if self._pending_since is not None and (time.monotonic() - self._pending_since) >= self.debounce_seconds:
            self._trigger_rebuild()

        return found_work

    def _trigger_rebuild(self) -> None:
        if not self._rebuild_lock.acquire(blocking=False):
            # Concurrency limit: a rebuild is already in progress (e.g. triggered
            # from another thread, such as api/realtime_api.py's on-demand
            # refresh). New work stays pending and will be picked up by the
            # next debounce window once the current cycle finishes.
            print("  rebuild already in progress - deferring to the next debounce window.")
            return
        try:
            self._run_rebuild_and_compute()
            self._pending_since = None
        finally:
            self._rebuild_lock.release()

    def _run_rebuild_and_compute(self) -> None:
        """Ingest -> staging/marts -> Polars compute, then (Phase 5,
        PHASE5-MONITORING.md Section 7) monitoring metrics -> schema drift
        scan -> anomaly detection, all as one cycle.

        Each of the three ingest/ELT/compute stages is wrapped individually:
        before Phase 5, an exception anywhere in this method propagated all
        the way up through poll_once() into run_forever()'s main loop, which
        has no try/except around flow.poll_once() - so a single bad cycle
        crashed the entire long-lived process. Section 7 asks for "alert
        dispatch on failures", which only makes sense if the process survives
        long enough to keep alerting on the *next* cycle too - so a caught
        stage failure now dispatches the matching ingestion_failure /
        elt_failure / compute_failure alert and returns, leaving this cycle's
        `_pending_since` marker in place so the next debounce window retries
        automatically instead of the work being silently dropped."""
        from compute.polars.compute_metrics import persist_compute_metrics
        from ingestion.batch_ingestion import ingest_all_batches
        from ingestion.event_ingestion import ingest_all_events
        from ingestion.load_duckdb import rebuild_warehouse

        started_at = utc_now()
        print(f"  [{started_at}] running incremental refresh: ingest -> staging/marts -> Polars compute...")

        try:
            batch_runs = ingest_all_batches()
            event_runs = ingest_all_events()
        except Exception as exc:  # noqa: BLE001 - see docstring: caught so the service keeps running
            self._dispatch_failure_alert("ingestion_failure", exc)
            return

        try:
            rebuild_warehouse()
        except Exception as exc:  # noqa: BLE001
            self._dispatch_failure_alert("elt_failure", exc)
            return

        try:
            persist_compute_metrics()
        except Exception as exc:  # noqa: BLE001
            self._dispatch_failure_alert("compute_failure", exc)
            return

        completed_at = utc_now()

        self.status.rebuilds_run += 1
        self.status.last_rebuild_at = completed_at
        upsert_lineage_edges(
            [
                LineageEdge(
                    run_id=f"realtime_flow_cycle_{self.status.cycles_run}",
                    source_node="orchestration://realtime_flow",
                    target_node="marts.*",
                    edge_type="realtime_orchestration_refresh",
                    entity="warehouse",
                    created_at=completed_at,
                )
            ]
        )
        print(
            f"  [{completed_at}] refresh complete: {len(batch_runs)} batch run(s), "
            f"{len(event_runs)} event run(s), warehouse + compute rebuilt."
        )

        self._run_monitoring_pass()

    def _dispatch_failure_alert(self, alert_type: str, exc: Exception) -> None:
        print(f"  [{utc_now()}] {alert_type} during refresh cycle {self.status.cycles_run}: {exc!r}")
        try:
            from alerts.dispatcher import dispatch_alert

            dispatch_alert(
                alert_type,
                entity="orchestration://realtime_flow",
                message=f"{alert_type} during refresh cycle {self.status.cycles_run}: {exc!r}",
                severity="critical",
            )
        except Exception as dispatch_exc:  # noqa: BLE001 - alerting must never take the service down
            print(f"  could not dispatch {alert_type} alert: {dispatch_exc!r}")

    def _run_monitoring_pass(self) -> None:
        """Phase 5, Section 7: "anomaly detection after compute" and
        "monitoring metric updates after ingestion/ELT/compute", run right
        after a successful refresh rather than on a separate timer - there is
        no new warehouse state to detect anomalies in between refresh cycles
        anyway. Each stage is isolated exactly like the ingest/ELT/compute
        stages above: a monitoring failure must never take down the
        real-time pipeline it's supposed to be watching."""
        try:
            from monitoring.metrics import run_all_metrics

            run_all_metrics()
        except Exception as exc:  # noqa: BLE001
            print(f"  monitoring metrics pass failed: {exc!r}")

        try:
            from monitoring.schema_drift import persist_drift_events, scan_quarantine_for_drift

            persist_drift_events(scan_quarantine_for_drift())
        except Exception as exc:  # noqa: BLE001
            print(f"  schema drift scan failed: {exc!r}")

        try:
            from anomalies.detector import run_all_detectors

            run_all_detectors()
        except Exception as exc:  # noqa: BLE001
            print(f"  anomaly detection pass failed: {exc!r}")

    def force_refresh(self) -> bool:
        """Public hook for an on-demand refresh (e.g. api/realtime_api.py),
        bypassing the debounce wait but still respecting the concurrency
        lock. Returns False without doing anything if a rebuild is already
        running."""
        if not self._rebuild_lock.acquire(blocking=False):
            return False
        try:
            self._run_rebuild_and_compute()
            self._pending_since = None
            return True
        finally:
            self._rebuild_lock.release()

    def close(self) -> None:
        if self.watcher is not None:
            self.watcher.close()

    def status_dict(self) -> dict:
        return {
            "cycles_run": self.status.cycles_run,
            "rebuilds_run": self.status.rebuilds_run,
            "last_cycle_at": self.status.last_cycle_at,
            "last_rebuild_at": self.status.last_rebuild_at,
            "last_new_file_count": self.status.last_new_file_count,
            "last_mongo_run_count": self.status.last_mongo_run_count,
            "mongo_enabled": self.status.mongo_enabled,
        }


_SHUTDOWN_REQUESTED = False


def _request_shutdown(signum, frame) -> None:  # noqa: ARG001
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True


def run_forever(
    *,
    poll_interval: float = 2.0,
    debounce_seconds: float = 5.0,
    duration_seconds: float | None = None,
    mongo_enabled: bool = True,
) -> RealtimeFlow:
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = False
    flow = RealtimeFlow(debounce_seconds=debounce_seconds, mongo_enabled=mongo_enabled)
    print(
        f"Real-time orchestration running: poll every {poll_interval:.1f}s, "
        f"debounce {debounce_seconds:.1f}s."
        + (f" Duration cap: {duration_seconds:.0f}s." if duration_seconds else " Running until interrupted (Ctrl+C).")
    )

    previous_handlers = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[sig] = signal.signal(sig, _request_shutdown)
        except (ValueError, OSError):
            pass

    start = time.monotonic()
    try:
        while True:
            if _SHUTDOWN_REQUESTED:
                print("Shutdown requested - stopping after this cycle...")
                break
            if duration_seconds is not None and (time.monotonic() - start) >= duration_seconds:
                print(f"Duration cap of {duration_seconds:.0f}s reached.")
                break
            flow.poll_once()
            write_heartbeat("realtime_flow", {"status": "running", **flow.status_dict()})
            time.sleep(poll_interval)
    finally:
        write_heartbeat("realtime_flow", {"status": "stopped", **flow.status_dict()})
        flow.close()
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    print(f"Stopped. {flow.status.cycles_run} cycles, {flow.status.rebuilds_run} rebuilds.")
    return flow


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real-time orchestration: detect new data, refresh warehouse + compute incrementally.")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Seconds between detection sweeps.")
    parser.add_argument("--debounce-seconds", type=float, default=5.0, help="Seconds of quiet-after-first-new-item before triggering a rebuild.")
    parser.add_argument("--duration-seconds", type=float, default=None, help="Stop after N seconds (default: run until Ctrl+C).")
    parser.add_argument("--no-mongo", action="store_true", help="Skip MongoDB change-stream detection even if MONGO_PASSWORD is set.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    run_forever(
        poll_interval=args.poll_interval,
        debounce_seconds=args.debounce_seconds,
        duration_seconds=args.duration_seconds,
        mongo_enabled=not args.no_mongo,
    )


if __name__ == "__main__":
    main()
