"""Monitoring Metrics & Reliability Tracking (PHASE5-MONITORING.md Section 3).

Every metric here is derived from tables/files this repo already writes -
`ingestion_runs`, `elt_model_runs`, `marts.compute_model_runs`, and the
Phase 4 streaming services' filesystem heartbeats (ingestion/heartbeat.py) -
rather than requiring new instrumentation scattered across every ingestion/
ELT/compute call site. `compute/polars/compute_metrics.py` gained real
per-model `duration_ms`/`started_at` tracking (Phase 5) specifically so
"compute run duration" here reflects real wall-clock time instead of being
unavailable.

Streaming metrics are the one category with no native time-series to read:
this filesystem+DuckDB architecture has no message queue with a real
"backlog depth". `_streaming_metrics()` computes event *rates* by diffing
each heartbeat's cumulative counters against the previous call's counters
(persisted to data/state/_monitoring_metrics_baseline.json, the same
small-state-file pattern used throughout Phase 4/5), and treats "backlog"
and "lag" as clearly-labeled proxies (realtime_flow's last poll's new-item
count, and time since its last successful rebuild) rather than fabricating
numbers a real queue would provide. See each function's docstring for the
specific proxy and why.

`run_all_metrics()` computes every category, persists to
`monitoring.system_metrics` with lineage, and dispatches the two
threshold-driven alerts explicitly scoped to metrics
(`ingestion_latency_threshold_exceeded`, `quarantine_rate_spike`, per
config/alerts.yaml's `thresholds:`) plus two heartbeat-derived health alerts
(`mongo_change_stream_disconnect`, `synthetic_generator_failure`) when a
streaming service's heartbeat has gone stale. Genuine pipeline *failures*
(ingestion_failure/elt_failure/compute_failure) are dispatched from
orchestration/realtime_flow.py instead (Section 7), where the actual
try/except around each pipeline stage lives - this module only ever reads
already-completed run history, so it has no vantage point on an
in-progress failure.
"""

from __future__ import annotations

import json
import statistics
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from ingestion.duckdb_utils import connect_with_retry
from ingestion.heartbeat import heartbeat_status, read_heartbeat
from ingestion.metadata import LineageEdge, upsert_lineage_edges, parse_utc, utc_now
from ingestion.paths import DUCKDB_PATH, STATE_DIR

METRICS_BASELINE_STATE_PATH = STATE_DIR / "_monitoring_metrics_baseline.json"


@dataclass(frozen=True)
class SystemMetric:
    metric_id: str
    metric_category: str  # ingestion | elt | compute | streaming
    metric_name: str
    metric_value: float
    unit: str
    computed_at: str
    window_start: str | None
    window_end: str | None
    metadata: dict[str, Any]


def _metric_id(category: str, name: str) -> str:
    compact = utc_now().replace(":", "").replace("-", "").replace(".", "").replace("+", "z")
    return f"metric_{category}_{name}_{compact}_{uuid.uuid4().hex[:8]}"


def _metric(
    category: str, name: str, value: float, unit: str,
    *, window_start: str | None = None, window_end: str | None = None, metadata: dict[str, Any] | None = None,
) -> SystemMetric:
    return SystemMetric(
        metric_id=_metric_id(category, name),
        metric_category=category,
        metric_name=name,
        metric_value=float(value),
        unit=unit,
        computed_at=utc_now(),
        window_start=window_start,
        window_end=window_end,
        metadata=metadata or {},
    )


def _safe(label: str, fn: Callable[[], list[SystemMetric]]) -> list[SystemMetric]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - one metric category failing shouldn't block the rest
        print(f"  monitoring metric group '{label}' failed: {exc!r}")
        return []


# ---------------------------------------------------------------------------
# Ingestion metrics
# ---------------------------------------------------------------------------


def ingestion_metrics(con, *, sample_size: int = 200) -> list[SystemMetric]:
    rows = con.execute(
        """
        select started_at, completed_at, duration_ms, valid_count, invalid_count, status
        from ingestion_runs
        order by completed_at desc
        limit ?
        """,
        [sample_size],
    ).fetchall()
    if not rows:
        return []

    durations = [row[2] for row in rows if row[2] is not None]
    avg_latency_ms = statistics.fmean(durations) if durations else 0.0

    completed_ats = [row[1] for row in rows if row[1] is not None]
    window_start = min(completed_ats) if completed_ats else None
    window_end = max(completed_ats) if completed_ats else None
    throughput_per_hour = 0.0
    if window_start and window_end:
        try:
            span_hours = max((parse_utc(str(window_end)) - parse_utc(str(window_start))).total_seconds() / 3600.0, 1e-6)
            throughput_per_hour = len(rows) / span_hours
        except (ValueError, TypeError):
            pass

    error_runs = sum(1 for row in rows if row[5] not in ("success", None))
    error_rate = error_runs / len(rows)

    total_valid = sum(row[3] or 0 for row in rows)
    total_invalid = sum(row[4] or 0 for row in rows)
    quarantine_rate = total_invalid / max(total_valid + total_invalid, 1)

    metrics = [
        _metric("ingestion", "ingestion_latency_avg_ms", avg_latency_ms, "ms", metadata={"sample_size": len(durations)}),
        _metric("ingestion", "ingestion_throughput_runs_per_hour", throughput_per_hour, "runs/hour",
                window_start=str(window_start) if window_start else None,
                window_end=str(window_end) if window_end else None, metadata={"sample_size": len(rows)}),
        _metric("ingestion", "ingestion_error_rate", error_rate, "ratio", metadata={"sample_size": len(rows), "error_runs": error_runs}),
        _metric("ingestion", "quarantine_rate", quarantine_rate, "ratio",
                metadata={"valid_count": total_valid, "invalid_count": total_invalid}),
    ]

    schema_drift_count = _table_count_since(con, "monitoring.schema_drift_events", "detected_at", hours=24)
    if schema_drift_count is not None:
        metrics.append(_metric("ingestion", "schema_drift_frequency_24h", schema_drift_count, "events"))

    change_stream_heartbeat = read_heartbeat("mongo_change_stream")
    if change_stream_heartbeat and change_stream_heartbeat.get("written_at"):
        try:
            age_seconds = (datetime.now(UTC) - parse_utc(str(change_stream_heartbeat["written_at"]))).total_seconds()
            metrics.append(
                _metric("ingestion", "change_stream_lag_seconds", age_seconds, "seconds",
                        metadata={"note": "seconds since mongo_change_stream's last heartbeat write"})
            )
        except ValueError:
            pass

    return metrics


def _table_count_since(con, table: str, ts_column: str, *, hours: int) -> int | None:
    """Returns None (rather than 0) if the table doesn't exist yet - lets
    every caller distinguish "genuinely zero" from "not created yet"."""
    try:
        row = con.execute(f"select count(*) from {table} where {ts_column} > current_timestamp - interval '{hours} hours'").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ELT metrics
# ---------------------------------------------------------------------------


def elt_metrics(con, *, sample_size: int = 200) -> list[SystemMetric]:
    rows = con.execute(
        """
        select model_name, high_watermark, started_at, completed_at, status, affected_key_count
        from elt_model_runs
        order by completed_at desc
        limit ?
        """,
        [sample_size],
    ).fetchall()
    if not rows:
        return []

    durations = []
    watermark_lags = []
    for _, high_watermark, started_at, completed_at, _, _ in rows:
        try:
            if started_at and completed_at:
                durations.append((parse_utc(str(completed_at)) - parse_utc(str(started_at))).total_seconds())
            if high_watermark and completed_at:
                watermark_lags.append((parse_utc(str(completed_at)) - parse_utc(str(high_watermark))).total_seconds())
        except (ValueError, TypeError):
            continue

    avg_duration = statistics.fmean(durations) if durations else 0.0
    failure_rate = sum(1 for row in rows if row[4] != "success") / len(rows)
    avg_incremental_volume = statistics.fmean([row[5] or 0 for row in rows])
    avg_watermark_lag = statistics.fmean(watermark_lags) if watermark_lags else 0.0

    return [
        _metric("elt", "elt_run_duration_avg_seconds", avg_duration, "seconds", metadata={"sample_size": len(durations)}),
        _metric("elt", "elt_failure_rate", failure_rate, "ratio", metadata={"sample_size": len(rows)}),
        _metric("elt", "elt_incremental_volume_avg", avg_incremental_volume, "rows", metadata={"sample_size": len(rows)}),
        _metric("elt", "elt_high_watermark_lag_avg_seconds", avg_watermark_lag, "seconds",
                metadata={"sample_size": len(watermark_lags), "note": "time between a model's high_watermark (event-time) and when it completed (processing-time)"}),
    ]


# ---------------------------------------------------------------------------
# Compute metrics
# ---------------------------------------------------------------------------


def compute_layer_metrics(con, *, sample_size: int = 200) -> list[SystemMetric]:
    try:
        rows = con.execute(
            """
            select model_name, status, duration_ms, row_count
            from marts.compute_model_runs
            order by computed_at desc
            limit ?
            """,
            [sample_size],
        ).fetchall()
    except Exception:
        return []  # duration_ms column not migrated in yet (pre-Phase-5 warehouse, never rebuilt) - nothing to report
    if not rows:
        return []

    durations = [row[2] for row in rows if row[2] is not None]
    avg_duration_seconds = (statistics.fmean(durations) / 1000.0) if durations else 0.0
    failure_rate = sum(1 for row in rows if row[1] != "success") / len(rows)
    avg_incremental_volume = statistics.fmean([row[3] or 0 for row in rows])

    return [
        _metric("compute", "compute_run_duration_avg_seconds", avg_duration_seconds, "seconds", metadata={"sample_size": len(durations)}),
        _metric("compute", "compute_failure_rate", failure_rate, "ratio", metadata={"sample_size": len(rows)}),
        _metric("compute", "compute_incremental_volume_avg", avg_incremental_volume, "rows", metadata={"sample_size": len(rows)}),
    ]


# ---------------------------------------------------------------------------
# Streaming metrics (filesystem heartbeats, not DuckDB - see module docstring)
# ---------------------------------------------------------------------------


def _load_baseline(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_baseline(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _rate_since_baseline(baseline: dict[str, Any], key: str, current_total: float, now_iso: str) -> float | None:
    """Events-per-minute since the last time this function was called with
    the same key, using the persisted baseline. Returns None on the first
    ever call (no prior sample to diff against)."""
    previous = baseline.get(key)
    if previous is None:
        return None
    try:
        elapsed_seconds = (parse_utc(now_iso) - parse_utc(previous["at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return None
    if elapsed_seconds <= 0:
        return None
    delta = current_total - previous.get("total", current_total)
    if delta < 0:
        delta = current_total  # counter reset (service restarted) - treat as a fresh count from zero
    return (delta / elapsed_seconds) * 60.0


def streaming_metrics(*, state_path: Path = METRICS_BASELINE_STATE_PATH, heartbeat_stale_seconds: float = 30.0) -> list[SystemMetric]:
    now_iso = utc_now()
    baseline = _load_baseline(state_path)
    metrics: list[SystemMetric] = []

    stream_gen = read_heartbeat("stream_generator")
    if stream_gen:
        counts = (stream_gen.get("counts") or {})
        total_events = float(sum(counts.values())) if isinstance(counts, dict) else 0.0
        rate = _rate_since_baseline(baseline, "stream_generator", total_events, now_iso)
        if rate is not None:
            metrics.append(_metric("streaming", "synthetic_generator_event_rate_per_min", rate, "events/min"))
        baseline["stream_generator"] = {"at": now_iso, "total": total_events}

    change_stream = read_heartbeat("mongo_change_stream")
    if change_stream:
        total_runs = float(change_stream.get("ingestion_runs_total", 0))
        rate = _rate_since_baseline(baseline, "mongo_change_stream", total_runs, now_iso)
        if rate is not None:
            metrics.append(_metric("streaming", "mongo_change_stream_event_rate_per_min", rate, "events/min"))
        baseline["mongo_change_stream"] = {"at": now_iso, "total": total_runs}

    realtime_flow = read_heartbeat("realtime_flow")
    if realtime_flow:
        backlog = float(realtime_flow.get("last_new_file_count", 0)) + float(realtime_flow.get("last_mongo_run_count", 0))
        metrics.append(
            _metric("streaming", "streaming_backlog", backlog, "items",
                    metadata={"note": "new files + new Mongo change-stream docs seen at realtime_flow's last poll - a proxy, not a true queue depth"})
        )
        last_rebuild_at = realtime_flow.get("last_rebuild_at")
        if last_rebuild_at:
            try:
                lag_seconds = (datetime.now(UTC) - parse_utc(str(last_rebuild_at))).total_seconds()
                metrics.append(
                    _metric("streaming", "streaming_lag_seconds", lag_seconds, "seconds",
                            metadata={"note": "time since orchestration/realtime_flow.py's last successful warehouse refresh"})
                )
            except ValueError:
                pass

    _save_baseline(state_path, baseline)

    # Heartbeat-derived service health alerts (see module docstring for why
    # these two live here rather than in realtime_flow.py's failure handling).
    _dispatch_heartbeat_alerts(fresh_within_seconds=heartbeat_stale_seconds)

    return metrics


def _dispatch_heartbeat_alerts(*, fresh_within_seconds: float) -> None:
    from alerts.dispatcher import dispatch_alert

    mongo_status = heartbeat_status("mongo_change_stream", fresh_within_seconds=fresh_within_seconds)
    if mongo_status["status"] == "stale":
        try:
            dispatch_alert(
                "mongo_change_stream_disconnect",
                entity="ingestion://mongo_change_stream",
                message=f"mongo_change_stream heartbeat is stale (last seen {mongo_status['last_heartbeat_at']}) - the watcher likely stopped or lost its connection.",
                severity="warning",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  could not dispatch mongo_change_stream_disconnect alert: {exc!r}")

    generator_status = heartbeat_status("stream_generator", fresh_within_seconds=fresh_within_seconds)
    if generator_status["status"] == "stale":
        try:
            dispatch_alert(
                "synthetic_generator_failure",
                entity="synthetic://stream_generator",
                message=f"stream_generator heartbeat is stale (last seen {generator_status['last_heartbeat_at']}) - it was running and appears to have stopped unexpectedly.",
                severity="critical",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  could not dispatch synthetic_generator_failure alert: {exc!r}")


# ---------------------------------------------------------------------------
# Threshold-driven metric alerts (distinct from anomalies/detector.py's
# statistical detection - these are simple fixed-threshold comparisons
# against config/alerts.yaml's `thresholds:` block).
# ---------------------------------------------------------------------------


def _dispatch_threshold_alerts(metrics: list[SystemMetric]) -> None:
    from alerts.dispatcher import dispatch_alert, load_alerts_config

    config = load_alerts_config()
    by_name = {m.metric_name: m for m in metrics}

    latency = by_name.get("ingestion_latency_avg_ms")
    threshold_seconds = config.thresholds.get("ingestion_latency_seconds")
    if latency is not None and threshold_seconds is not None and (latency.metric_value / 1000.0) > threshold_seconds:
        try:
            dispatch_alert(
                "ingestion_latency_threshold_exceeded",
                entity="ingestion",
                message=f"Average ingestion latency {latency.metric_value / 1000.0:.1f}s exceeds the {threshold_seconds:.0f}s threshold.",
                metadata={"metric_id": latency.metric_id, "value_ms": latency.metric_value},
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  could not dispatch ingestion_latency_threshold_exceeded alert: {exc!r}")

    quarantine_rate = by_name.get("quarantine_rate")
    rate_threshold = config.thresholds.get("quarantine_rate")
    if quarantine_rate is not None and rate_threshold is not None and quarantine_rate.metric_value > rate_threshold:
        try:
            dispatch_alert(
                "quarantine_rate_spike",
                entity="ingestion",
                message=f"Quarantine rate {quarantine_rate.metric_value:.1%} exceeds the {rate_threshold:.0%} threshold.",
                metadata={"metric_id": quarantine_rate.metric_id, "value": quarantine_rate.metric_value},
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  could not dispatch quarantine_rate_spike alert: {exc!r}")


# ---------------------------------------------------------------------------
# Persistence + orchestration entry point
# ---------------------------------------------------------------------------


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists monitoring")
    con.execute(
        """
        create table if not exists monitoring.system_metrics (
          metric_id varchar primary key,
          metric_category varchar,
          metric_name varchar,
          metric_value double,
          unit varchar,
          computed_at timestamptz,
          window_start timestamptz,
          window_end timestamptz,
          metadata varchar
        )
        """
    )


def persist_metrics(metrics: list[SystemMetric], db_path: Path = DUCKDB_PATH) -> None:
    if not metrics:
        return
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.executemany(
            """
            insert or replace into monitoring.system_metrics
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    m.metric_id, m.metric_category, m.metric_name, m.metric_value, m.unit,
                    m.computed_at, m.window_start, m.window_end,
                    json.dumps(m.metadata, default=str, sort_keys=True),
                )
                for m in metrics
            ],
        )
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=m.metric_id,
                source_node=f"monitoring_metrics://{m.metric_category}",
                target_node="monitoring.system_metrics",
                edge_type="monitoring_metric_recorded",
                entity=m.metric_category,
                created_at=m.computed_at,
            )
            for m in metrics
        ]
    )


def run_all_metrics(db_path: Path = DUCKDB_PATH) -> list[SystemMetric]:
    metrics: list[SystemMetric] = []
    if db_path.exists():
        with connect_with_retry(db_path, read_only=True) as con:
            metrics.extend(_safe("ingestion", lambda: ingestion_metrics(con)))
            metrics.extend(_safe("elt", lambda: elt_metrics(con)))
            metrics.extend(_safe("compute", lambda: compute_layer_metrics(con)))
    metrics.extend(_safe("streaming", streaming_metrics))

    persist_metrics(metrics, db_path)
    _dispatch_threshold_alerts(metrics)
    return metrics


if __name__ == "__main__":
    results = run_all_metrics()
    print(f"Recorded {len(results)} system metrics")
    for metric in results:
        print(f"  [{metric.metric_category}] {metric.metric_name} = {metric.metric_value:.4g} {metric.unit}")
