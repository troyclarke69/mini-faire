"""Anomaly Detection Engine (PHASE5-MONITORING.md Section 1).

Eight detectors, each reading already-materialized warehouse tables
(`marts.*`, `ingestion_runs`) through a single read-only DuckDB connection so
one call to `run_all_detectors()` is one consistent snapshot of the
warehouse, not eight separately-timed reads:

- `detect_gmv_anomalies` / `detect_order_velocity_anomalies`: z-score of
  today's daily total against a rolling mean/std baseline from
  `marts.metrics_retailer_daily`.
- `detect_inventory_stockouts`: percentile-threshold on `marts.dim_product`'s
  current inventory_count (plus a hard rule for count == 0).
- `detect_price_anomalies`: percentile-threshold on `price_changed` events'
  percent price change in `marts.fact_product_events`.
- `detect_event_lag_spikes`: z-score of recent vs. historical *ingestion*
  lag (`loaded_at - event_ts`, the same quantity `compute/polars/
  transform_event_lag.py` already reports) per event_type in
  `marts.fact_orders_events`.
- `detect_retailer_health_degradation`: EWMA-baseline percent drop in
  `marts.compute_retailer_health`'s score. This table is `create or replace`
  each compute run (see compute/polars/compute_metrics.py) - there is no
  built-in history to compute a baseline from, so this detector persists its
  own rolling EWMA per retailer to a small state file (data/state/
  _anomaly_baseline.json), the same pattern ingestion/mongo_change_stream.py
  uses for resume tokens.
- `detect_ingestion_volume_anomalies`: z-score of this hour's ingestion run
  count against a rolling mean/std baseline from `ingestion_runs`.
- `detect_quarantine_rate_anomalies`: percentile/threshold on each entity's
  cumulative invalid/(valid+invalid) ratio from `ingestion_runs`.

Together these exercise all four required statistical methods (rolling
mean+std, EWMA, percentile thresholds, z-score). An ML-based detector is
explicitly out of scope here, per PHASE5-MONITORING.md's own "(later phase)"
note on that method.

Every anomaly found is persisted to `anomalies.anomaly_events` (with a
`anomaly_detected` lineage edge per row) and, unless `dispatch=False`, routed
through `alerts/dispatcher.py` as an `anomaly_detected` alert - one call to
`run_all_detectors()` does detection, persistence, lineage, and alerting in
one pass, which is exactly what orchestration/realtime_flow.py's Section 7
integration calls after every refresh cycle.

A detector raising (e.g. a table not existing yet on a fresh checkout) never
takes down the others or the caller - each runs in its own try/except inside
run_all_detectors(), matching orchestration/realtime_flow.py's existing
"one bad source shouldn't stop the pipeline" philosophy.
"""

from __future__ import annotations

import json
import statistics
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DUCKDB_PATH, STATE_DIR

ANOMALY_STATE_PATH = STATE_DIR / "_anomaly_baseline.json"

# Shared thresholds. Deliberately module-level constants rather than a config
# file (unlike alerts.yaml/mongo.yaml) - these are statistical tuning knobs
# for a demo dataset, not operational secrets or environment-specific
# settings, and every detector function still accepts overrides as kwargs for
# testing.
Z_WARN = 2.0
Z_CRIT = 3.5


# ---------------------------------------------------------------------------
# Statistics helpers (the four required detection methods)
# ---------------------------------------------------------------------------


def rolling_mean_std(values: list[float]) -> tuple[float, float]:
    """Method 1: rolling mean + population standard deviation. Population
    (not sample) stdev is used deliberately - this repo's baselines are often
    small-n (a couple weeks of daily rows), where sample stdev's n-1 term
    makes an already-noisy estimate noisier without adding real precision."""
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return float(values[0]), 0.0
    return statistics.fmean(values), statistics.pstdev(values)


def ewma_update(previous: float | None, value: float, alpha: float = 0.3) -> float:
    """Method 2: exponentially weighted moving average. With no prior value,
    the series is seeded at the first observation (matches every standard
    EWMA initialization - there is no "before the first data point" average
    to weight against)."""
    if previous is None:
        return float(value)
    return alpha * float(value) + (1 - alpha) * previous


def percentile(values: list[float], pct: float) -> float:
    """Method 3: percentile-based threshold, linear interpolation between
    the two nearest ranks (the same convention numpy's default and Excel's
    PERCENTILE.INC use)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def zscore(value: float, mean: float, std: float) -> float | None:
    """Method 4: z-score. Returns None (rather than +/-inf or a divide error)
    when std is 0 - a baseline with zero variance can't produce a meaningful
    z-score, and every caller below treats None as "can't evaluate, skip"."""
    if std == 0:
        return None
    return (value - mean) / std


def _severity_from_magnitude(magnitude: float, warn: float = Z_WARN, crit: float = Z_CRIT) -> str:
    if magnitude >= crit:
        return "critical"
    if magnitude >= warn:
        return "warning"
    return "info"


# ---------------------------------------------------------------------------
# Small JSON state file helpers (same pattern as ingestion/mongo_change_stream.py's
# resume tokens and ingestion/heartbeat.py's heartbeat files).
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Anomaly record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Anomaly:
    anomaly_id: str
    anomaly_type: str
    severity: str
    detected_at: str
    entity_type: str
    entity_id: str
    metric_name: str
    metric_value: float
    baseline_value: float | None
    deviation: float | None
    method: str
    metadata: dict[str, Any]


def _anomaly_id(anomaly_type: str, entity_id: str) -> str:
    compact = utc_now().replace(":", "").replace("-", "").replace(".", "").replace("+", "z")
    safe_entity = "".join(ch if ch.isalnum() else "_" for ch in str(entity_id))[:40]
    return f"anom_{anomaly_type}_{safe_entity}_{compact}_{uuid.uuid4().hex[:8]}"


def _make_anomaly(
    *,
    anomaly_type: str,
    severity: str,
    entity_type: str,
    entity_id: str,
    metric_name: str,
    metric_value: float,
    baseline_value: float | None,
    deviation: float | None,
    method: str,
    metadata: dict[str, Any] | None = None,
) -> Anomaly:
    return Anomaly(
        anomaly_id=_anomaly_id(anomaly_type, entity_id),
        anomaly_type=anomaly_type,
        severity=severity,
        detected_at=utc_now(),
        entity_type=entity_type,
        entity_id=str(entity_id),
        metric_name=metric_name,
        metric_value=float(metric_value),
        baseline_value=float(baseline_value) if baseline_value is not None else None,
        deviation=float(deviation) if deviation is not None else None,
        method=method,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def detect_gmv_anomalies(con, *, z_warn: float = Z_WARN, z_crit: float = Z_CRIT, lookback_days: int = 14) -> list[Anomaly]:
    rows = con.execute(
        """
        select order_date, sum(gmv) as gmv
        from marts.metrics_retailer_daily
        group by order_date
        order by order_date
        """
    ).fetchall()
    if len(rows) < 4:
        return []
    *history, current = rows
    baseline_values = [float(row[1] or 0) for row in history[-lookback_days:]]
    mean, std = rolling_mean_std(baseline_values)
    z = zscore(float(current[1] or 0), mean, std)
    if z is None or abs(z) < z_warn:
        return []
    direction = "spike" if z > 0 else "drop"
    return [
        _make_anomaly(
            anomaly_type=f"gmv_{direction}",
            severity=_severity_from_magnitude(abs(z), z_warn, z_crit),
            entity_type="system",
            entity_id="marketplace",
            metric_name="daily_gmv",
            metric_value=current[1] or 0,
            baseline_value=mean,
            deviation=z,
            method="zscore",
            metadata={"order_date": str(current[0]), "lookback_days": len(baseline_values)},
        )
    ]


def detect_order_velocity_anomalies(con, *, z_warn: float = Z_WARN, z_crit: float = Z_CRIT, lookback_days: int = 14) -> list[Anomaly]:
    rows = con.execute(
        """
        select order_date, sum(order_count) as order_count
        from marts.metrics_retailer_daily
        group by order_date
        order by order_date
        """
    ).fetchall()
    if len(rows) < 4:
        return []
    *history, current = rows
    baseline_values = [float(row[1] or 0) for row in history[-lookback_days:]]
    mean, std = rolling_mean_std(baseline_values)
    z = zscore(float(current[1] or 0), mean, std)
    if z is None or abs(z) < z_warn:
        return []
    return [
        _make_anomaly(
            anomaly_type="order_velocity_change",
            severity=_severity_from_magnitude(abs(z), z_warn, z_crit),
            entity_type="system",
            entity_id="marketplace",
            metric_name="daily_order_count",
            metric_value=current[1] or 0,
            baseline_value=mean,
            deviation=z,
            method="zscore",
            metadata={"order_date": str(current[0]), "lookback_days": len(baseline_values)},
        )
    ]


def detect_inventory_stockouts(con, *, low_percentile: float = 10.0) -> list[Anomaly]:
    rows = con.execute(
        "select product_id, product_name, inventory_count from marts.dim_product where is_active"
    ).fetchall()
    if not rows:
        return []
    counts = [float(row[2] or 0) for row in rows]
    threshold = percentile(counts, low_percentile)
    anomalies: list[Anomaly] = []
    for product_id, product_name, inventory_count in rows:
        count = float(inventory_count or 0)
        if count <= 0:
            anomalies.append(
                _make_anomaly(
                    anomaly_type="inventory_stockout",
                    severity="critical",
                    entity_type="product",
                    entity_id=product_id,
                    metric_name="inventory_count",
                    metric_value=count,
                    baseline_value=threshold,
                    deviation=None,
                    method="percentile_threshold",
                    metadata={"product_name": product_name, "percentile": low_percentile},
                )
            )
        elif count <= threshold:
            anomalies.append(
                _make_anomaly(
                    anomaly_type="inventory_stockout",
                    severity="warning",
                    entity_type="product",
                    entity_id=product_id,
                    metric_name="inventory_count",
                    metric_value=count,
                    baseline_value=threshold,
                    deviation=None,
                    method="percentile_threshold",
                    metadata={"product_name": product_name, "percentile": low_percentile},
                )
            )
    return anomalies


def detect_price_anomalies(con, *, pct_threshold: float = 0.30, high_percentile: float = 95.0, lookback: int = 200) -> list[Anomaly]:
    rows = con.execute(
        """
        select event_id, product_id, old_price, new_price, event_ts
        from marts.fact_product_events
        where event_type = 'price_changed' and old_price is not null and old_price > 0
        order by event_ts desc
        limit ?
        """,
        [lookback],
    ).fetchall()
    if len(rows) < 5:
        return []
    changes = [
        (event_id, product_id, float(old_price), float(new_price), event_ts,
         (float(new_price) - float(old_price)) / float(old_price))
        for event_id, product_id, old_price, new_price, event_ts in rows
    ]
    magnitude_baseline = percentile([abs(c[5]) for c in changes], high_percentile)
    effective_threshold = max(pct_threshold, magnitude_baseline)
    anomalies: list[Anomaly] = []
    for event_id, product_id, old_price, new_price, event_ts, pct in changes:
        if abs(pct) < effective_threshold:
            continue
        anomalies.append(
            _make_anomaly(
                anomaly_type="price_anomaly",
                severity="critical" if abs(pct) >= effective_threshold * 2 else "warning",
                entity_type="product",
                entity_id=product_id,
                metric_name="price_pct_change",
                metric_value=pct,
                baseline_value=effective_threshold,
                deviation=None,
                method="percentile_threshold",
                metadata={
                    "event_id": event_id,
                    "old_price": old_price,
                    "new_price": new_price,
                    "event_ts": str(event_ts),
                },
            )
        )
    return anomalies


def detect_event_lag_spikes(con, *, z_warn: float = Z_WARN, z_crit: float = Z_CRIT, recent_n: int = 20, baseline_n: int = 200) -> list[Anomaly]:
    rows = con.execute(
        """
        select event_type, date_diff('second', event_ts, loaded_at) as lag_seconds
        from marts.fact_orders_events
        order by event_ts desc
        limit ?
        """,
        [recent_n + baseline_n],
    ).fetchall()
    by_type: dict[str, list[float]] = {}
    for event_type, lag_seconds in rows:
        by_type.setdefault(event_type, []).append(float(lag_seconds or 0))

    anomalies: list[Anomaly] = []
    for event_type, lags in by_type.items():
        if len(lags) < recent_n + 5:
            continue
        current = lags[:recent_n]
        baseline = lags[recent_n:]
        mean, std = rolling_mean_std(baseline)
        current_mean = statistics.fmean(current)
        z = zscore(current_mean, mean, std)
        # Only lag SPIKES matter operationally (a lag drop just means
        # ingestion caught up faster than usual) - one-sided on purpose.
        if z is None or z < z_warn:
            continue
        anomalies.append(
            _make_anomaly(
                anomaly_type="event_lag_spike",
                severity=_severity_from_magnitude(z, z_warn, z_crit),
                entity_type="event_type",
                entity_id=event_type,
                metric_name="avg_ingestion_lag_seconds",
                metric_value=current_mean,
                baseline_value=mean,
                deviation=z,
                method="zscore",
                metadata={"sample_size": len(current), "baseline_size": len(baseline)},
            )
        )
    return anomalies


def detect_retailer_health_degradation(
    con, *, alpha: float = 0.3, drop_warn: float = 0.15, drop_crit: float = 0.35, state_path: Path = ANOMALY_STATE_PATH
) -> list[Anomaly]:
    rows = con.execute("select retailer_id, retailer_health_score from marts.compute_retailer_health").fetchall()
    state = _load_state(state_path)
    baseline: dict[str, float] = dict(state.get("retailer_health_ewma") or {})
    updated = dict(baseline)
    anomalies: list[Anomaly] = []

    for retailer_id, score in rows:
        score = float(score or 0)
        previous = baseline.get(retailer_id)
        if previous is not None and previous > 0:
            drop = (previous - score) / previous
            if drop >= drop_warn:
                anomalies.append(
                    _make_anomaly(
                        anomaly_type="retailer_health_degradation",
                        severity="critical" if drop >= drop_crit else "warning",
                        entity_type="retailer",
                        entity_id=retailer_id,
                        metric_name="retailer_health_score",
                        metric_value=score,
                        baseline_value=previous,
                        deviation=drop,
                        method="ewma",
                        metadata={"alpha": alpha},
                    )
                )
        updated[retailer_id] = ewma_update(previous, score, alpha)

    state["retailer_health_ewma"] = updated
    _save_state(state_path, state)
    return anomalies


def detect_ingestion_volume_anomalies(con, *, z_warn: float = Z_WARN, z_crit: float = Z_CRIT, lookback_hours: int = 24) -> list[Anomaly]:
    rows = con.execute(
        """
        select date_trunc('hour', completed_at) as hr, count(*) as run_count
        from ingestion_runs
        group by 1
        order by 1
        """
    ).fetchall()
    if len(rows) < 4:
        return []
    *history, current = rows
    baseline_values = [float(row[1] or 0) for row in history[-lookback_hours:]]
    mean, std = rolling_mean_std(baseline_values)
    z = zscore(float(current[1] or 0), mean, std)
    if z is None or abs(z) < z_warn:
        return []
    return [
        _make_anomaly(
            anomaly_type="ingestion_volume_anomaly",
            severity=_severity_from_magnitude(abs(z), z_warn, z_crit),
            entity_type="system",
            entity_id="ingestion",
            metric_name="ingestion_runs_per_hour",
            metric_value=current[1] or 0,
            baseline_value=mean,
            deviation=z,
            method="zscore",
            metadata={"hour": str(current[0]), "lookback_hours": len(baseline_values)},
        )
    ]


def detect_quarantine_rate_anomalies(con, *, rate_warn: float = 0.25, rate_crit: float = 0.5) -> list[Anomaly]:
    rows = con.execute(
        """
        select entity, sum(valid_count) as valid_count, sum(invalid_count) as invalid_count
        from ingestion_runs
        group by entity
        """
    ).fetchall()
    anomalies: list[Anomaly] = []
    for entity, valid_count, invalid_count in rows:
        valid_count = valid_count or 0
        invalid_count = invalid_count or 0
        total = valid_count + invalid_count
        if total == 0:
            continue
        rate = invalid_count / total
        if rate < rate_warn:
            continue
        anomalies.append(
            _make_anomaly(
                anomaly_type="quarantine_rate_anomaly",
                severity="critical" if rate >= rate_crit else "warning",
                entity_type="entity",
                entity_id=entity,
                metric_name="quarantine_rate",
                metric_value=rate,
                baseline_value=rate_warn,
                deviation=None,
                method="percentile_threshold",
                metadata={"valid_count": valid_count, "invalid_count": invalid_count},
            )
        )
    return anomalies


DETECTORS: tuple[Callable[..., list[Anomaly]], ...] = (
    detect_gmv_anomalies,
    detect_order_velocity_anomalies,
    detect_inventory_stockouts,
    detect_price_anomalies,
    detect_event_lag_spikes,
    detect_retailer_health_degradation,
    detect_ingestion_volume_anomalies,
    detect_quarantine_rate_anomalies,
)


# ---------------------------------------------------------------------------
# Persistence + orchestration entry point
# ---------------------------------------------------------------------------


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists anomalies")
    con.execute(
        """
        create table if not exists anomalies.anomaly_events (
          anomaly_id varchar primary key,
          anomaly_type varchar,
          severity varchar,
          detected_at timestamptz,
          entity_type varchar,
          entity_id varchar,
          metric_name varchar,
          metric_value double,
          baseline_value double,
          deviation double,
          method varchar,
          metadata varchar
        )
        """
    )


def persist_anomalies(anomalies: list[Anomaly], db_path: Path = DUCKDB_PATH) -> None:
    if not anomalies:
        return
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.executemany(
            """
            insert or replace into anomalies.anomaly_events
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    a.anomaly_id, a.anomaly_type, a.severity, a.detected_at,
                    a.entity_type, a.entity_id, a.metric_name, a.metric_value,
                    a.baseline_value, a.deviation, a.method,
                    json.dumps(a.metadata, default=str, sort_keys=True),
                )
                for a in anomalies
            ],
        )
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=a.anomaly_id,
                source_node=f"anomaly_detector://{a.anomaly_type}",
                target_node="anomalies.anomaly_events",
                edge_type="anomaly_detected",
                entity=a.entity_id,
                created_at=a.detected_at,
            )
            for a in anomalies
        ]
    )


def run_all_detectors(db_path: Path = DUCKDB_PATH, *, dispatch: bool = True) -> list[Anomaly]:
    """Run every detector against one consistent read-only snapshot of the
    warehouse, persist whatever's found, and (unless dispatch=False, used by
    tests/manual runs that don't want to touch alerting) dispatch one
    `anomaly_detected` alert per anomaly via alerts/dispatcher.py."""
    if not db_path.exists():
        return []

    found: list[Anomaly] = []
    with connect_with_retry(db_path, read_only=True) as con:
        for detector in DETECTORS:
            try:
                found.extend(detector(con))
            except Exception as exc:  # noqa: BLE001 - one bad detector shouldn't stop the rest
                print(f"  anomaly detector {detector.__name__} failed: {exc!r}")

    persist_anomalies(found, db_path)

    if dispatch and found:
        from alerts.dispatcher import dispatch_alert

        for anomaly in found:
            try:
                dispatch_alert(
                    "anomaly_detected",
                    entity=anomaly.entity_id,
                    message=(
                        f"{anomaly.anomaly_type} on {anomaly.entity_type}={anomaly.entity_id}: "
                        f"{anomaly.metric_name}={anomaly.metric_value:.4g}"
                        + (f" (baseline {anomaly.baseline_value:.4g})" if anomaly.baseline_value is not None else "")
                    ),
                    metadata=asdict(anomaly),
                    severity=anomaly.severity,
                    lineage_ref=f"anomalies.anomaly_events:{anomaly.anomaly_id}",
                )
            except Exception as exc:  # noqa: BLE001 - alerting must never break detection
                print(f"  could not dispatch alert for {anomaly.anomaly_id}: {exc!r}")

    return found


if __name__ == "__main__":
    results = run_all_detectors()
    print(f"Detected {len(results)} anomalies")
    for anomaly in results:
        print(f"  [{anomaly.severity}] {anomaly.anomaly_type} {anomaly.entity_type}={anomaly.entity_id}")
