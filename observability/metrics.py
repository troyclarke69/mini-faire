"""Observability: Prometheus metrics (PHASE7-DEPLOYMENT.md Section 8).

Tracks the nine signals Section 8 names: ingestion latency, ELT duration,
compute duration, ML inference duration, streaming lag, anomaly frequency,
tenant usage, API latency, API error rate.

Two halves, deliberately:

- A small hand-rolled Prometheus client (`Counter`/`Gauge`/`Histogram`,
  `render_prometheus_text()`) instead of the `prometheus_client` PyPI
  package - the same "hand-roll it with stdlib rather than add a
  dependency" call this repo already made for JWT/password hashing
  (`auth/auth_models.py`) and the webhook POST (`alerts/dispatcher.py`).
  `prometheus_client` isn't a heavy dependency, but the exposition text
  format (`# HELP` / `# TYPE` / `name{labels} value`) is small and stable
  enough that hand-rolling it keeps this module runnable with zero new
  installs, matching this whole repo's "the demo runs with zero
  configuration" posture - and it's what makes `MiniFaireRegistry` fully
  unit-testable in a sandbox with no network access to a package index.
- `refresh_from_warehouse()`, which does NOT recompute these signals from
  raw events - it reads them from tables Phase 5/6/7 already populate
  (`monitoring.system_metrics` from `monitoring/metrics.py`,
  `elt_model_runs` rows with `load_strategy='ml_inference'` from
  `orchestration/ml_inference_flow.py`, `anomalies.anomaly_events` from
  `anomalies/detector.py`, `marts.compute_tenant_health` from
  `compute/polars/tenant_metrics.py`) and re-exposes them in Prometheus
  format. Phase 5 already built the collection pipeline for
  ingestion/ELT/compute/streaming metrics; this module's job is exposing
  what's already collected to Prometheus/Grafana, not duplicating
  collection from scratch.

`ApiMetricsMiddleware` (api_request_duration_seconds / api_errors_total)
imports fastapi/starlette lazily, inside its own methods, not at module
level - so this file stays importable (and its Counter/Gauge/Histogram/
registry logic stays testable) in an environment without fastapi/starlette
installed, matching `ingestion/mongo_ingest.py`'s "import the optional heavy
dependency inside the function that needs it" convention rather than
`auth/auth_middleware.py`'s "this whole file requires fastapi" approach -
the difference is that unlike auth_middleware.py (which is nothing BUT
FastAPI dependencies), most of this file has real value with no framework
installed at all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ingestion.duckdb_utils import connect_with_retry
from ingestion.paths import DUCKDB_PATH

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

_LabelKey = tuple[tuple[str, str], ...]


def _label_key(labels: dict[str, str] | None) -> _LabelKey:
    return tuple(sorted((labels or {}).items()))


def _format_labels(labels: _LabelKey) -> str:
    if not labels:
        return ""
    pairs = ",".join(f'{name}="{value}"' for name, value in labels)
    return "{" + pairs + "}"


class Counter:
    """Monotonically increasing value - `anomaly_frequency_total`,
    `api_errors_total`."""

    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help_text = help_text
        self._values: dict[_LabelKey, float] = {}

    def inc(self, amount: float = 1.0, *, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels)
        self._values[key] = self._values.get(key, 0.0) + amount

    def set_total(self, value: float, *, labels: dict[str, str] | None = None) -> None:
        """Sets the counter to an absolute value rather than incrementing -
        used by `refresh_from_warehouse()`, which reads an already-final
        count (e.g. `select count(*) from anomalies.anomaly_events`) rather
        than replaying individual increments."""
        self._values[_label_key(labels)] = value

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} counter"]
        for labels, value in sorted(self._values.items()):
            lines.append(f"{self.name}{_format_labels(labels)} {value}")
        return lines


class Gauge:
    """A value that can go up or down - `streaming_lag_seconds`,
    `tenant_usage_gmv`."""

    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help_text = help_text
        self._values: dict[_LabelKey, float] = {}

    def set(self, value: float, *, labels: dict[str, str] | None = None) -> None:
        self._values[_label_key(labels)] = value

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} gauge"]
        for labels, value in sorted(self._values.items()):
            lines.append(f"{self.name}{_format_labels(labels)} {value}")
        return lines


# Default bucket boundaries for a duration histogram, seconds - covers
# sub-second API calls up through multi-minute ELT/ML runs in one set of
# buckets shared by every Histogram below unless overridden.
DEFAULT_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300)


class Histogram:
    """Cumulative bucket counts + sum + count, the standard Prometheus
    histogram shape (`_bucket{le=...}`, `_sum`, `_count`) - used for every
    *_duration_seconds metric below."""

    def __init__(self, name: str, help_text: str, *, buckets: tuple[float, ...] = DEFAULT_DURATION_BUCKETS):
        self.name = name
        self.help_text = help_text
        self.buckets = tuple(sorted(buckets)) + (float("inf"),)
        self._bucket_counts: dict[_LabelKey, list[int]] = {}
        self._sums: dict[_LabelKey, float] = {}
        self._counts: dict[_LabelKey, int] = {}

    def observe(self, value: float, *, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels)
        if key not in self._bucket_counts:
            self._bucket_counts[key] = [0] * len(self.buckets)
        for i, upper_bound in enumerate(self.buckets):
            if value <= upper_bound:
                self._bucket_counts[key][i] += 1
        self._sums[key] = self._sums.get(key, 0.0) + value
        self._counts[key] = self._counts.get(key, 0) + 1

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} histogram"]
        for labels, counts in sorted(self._bucket_counts.items()):
            base_labels = dict(labels)
            for upper_bound, count in zip(self.buckets, counts):
                le = "+Inf" if upper_bound == float("inf") else str(upper_bound)
                bucket_labels = _label_key({**base_labels, "le": le})
                lines.append(f"{self.name}_bucket{_format_labels(bucket_labels)} {count}")
            lines.append(f"{self.name}_sum{_format_labels(labels)} {self._sums[labels]}")
            lines.append(f"{self.name}_count{_format_labels(labels)} {self._counts[labels]}")
        return lines


@dataclass
class MiniFaireRegistry:
    """One instance per process - `default_registry()` below is the
    process-wide singleton every call site shares, matching
    `ml/registry.py`'s "one registry, many callers" shape (just in-memory
    here instead of DuckDB-backed, since Prometheus metrics are scraped
    fresh each interval rather than needing historical persistence - the
    historical record already lives in the warehouse tables this reads
    from)."""

    ingestion_latency_seconds: Histogram = field(
        default_factory=lambda: Histogram("mini_faire_ingestion_latency_seconds", "Ingestion run latency")
    )
    elt_duration_seconds: Histogram = field(
        default_factory=lambda: Histogram("mini_faire_elt_duration_seconds", "ELT model run duration")
    )
    compute_duration_seconds: Histogram = field(
        default_factory=lambda: Histogram("mini_faire_compute_duration_seconds", "Polars compute run duration")
    )
    ml_inference_duration_seconds: Histogram = field(
        default_factory=lambda: Histogram("mini_faire_ml_inference_duration_seconds", "ML inference stage duration")
    )
    streaming_lag_seconds: Gauge = field(
        default_factory=lambda: Gauge("mini_faire_streaming_lag_seconds", "Streaming event processing lag")
    )
    anomaly_frequency_total: Counter = field(
        default_factory=lambda: Counter("mini_faire_anomaly_frequency_total", "Anomaly events detected")
    )
    tenant_usage_gmv: Gauge = field(
        default_factory=lambda: Gauge("mini_faire_tenant_usage_gmv", "Per-tenant GMV (marts.compute_tenant_health)")
    )
    api_request_duration_seconds: Histogram = field(
        default_factory=lambda: Histogram("mini_faire_api_request_duration_seconds", "API request duration")
    )
    api_errors_total: Counter = field(
        default_factory=lambda: Counter("mini_faire_api_errors_total", "API responses with status >= 500")
    )

    def all_metrics(self) -> list[Counter | Gauge | Histogram]:
        return [
            self.ingestion_latency_seconds, self.elt_duration_seconds, self.compute_duration_seconds,
            self.ml_inference_duration_seconds, self.streaming_lag_seconds, self.anomaly_frequency_total,
            self.tenant_usage_gmv, self.api_request_duration_seconds, self.api_errors_total,
        ]

    def render_text(self) -> str:
        lines: list[str] = []
        for metric in self.all_metrics():
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"


_default_registry: MiniFaireRegistry | None = None


def default_registry() -> MiniFaireRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = MiniFaireRegistry()
    return _default_registry


def render_metrics_response(registry: MiniFaireRegistry | None = None) -> tuple[str, str]:
    """Returns (body, content_type) - what a `GET /observability/metrics`
    FastAPI route (mounted in api/metrics_api.py) hands back directly,
    without this module needing to import fastapi itself to build the
    response body."""
    registry = registry or default_registry()
    return registry.render_text(), PROMETHEUS_CONTENT_TYPE


# ---------------------------------------------------------------------------
# refresh_from_warehouse() - re-exposes already-collected signals, doesn't
# recompute them
# ---------------------------------------------------------------------------


def _safe_query(con, sql: str, params: list[Any] | None = None) -> list[tuple]:
    try:
        result = con.execute(sql, params) if params is not None else con.execute(sql)
        return result.fetchall()
    except Exception:
        return []


def refresh_from_warehouse(db_path: Path = DUCKDB_PATH, *, registry: MiniFaireRegistry | None = None) -> MiniFaireRegistry:
    """Reads the latest values for every metric above from whichever
    warehouse tables already carry them and updates `registry` in place.
    Safe to call against a fresh warehouse (every read is wrapped so a
    missing table - nothing has run yet - degrades to "no data for that
    metric" rather than crashing, the same read-only-connection defensive
    pattern `ml/registry.py`'s `get_active_model()` established)."""
    registry = registry or default_registry()
    if not db_path.exists():
        return registry

    with connect_with_retry(db_path, read_only=True) as con:
        # monitoring.system_metrics: ingestion/ELT/compute/streaming -
        # monitoring/metrics.py's exact metric_category/metric_name values.
        for row in _safe_query(
            con,
            "select metric_value from monitoring.system_metrics "
            "where metric_category = 'ingestion' and metric_name = 'ingestion_latency_avg_ms' "
            "order by computed_at desc limit 1",
        ):
            registry.ingestion_latency_seconds.observe(float(row[0]) / 1000.0)  # ms -> seconds

        for row in _safe_query(
            con,
            "select metric_value from monitoring.system_metrics "
            "where metric_category = 'elt' and metric_name = 'elt_run_duration_avg_seconds' "
            "order by computed_at desc limit 1",
        ):
            registry.elt_duration_seconds.observe(float(row[0]))

        for row in _safe_query(
            con,
            "select metric_value from monitoring.system_metrics "
            "where metric_category = 'compute' and metric_name = 'compute_run_duration_avg_seconds' "
            "order by computed_at desc limit 1",
        ):
            registry.compute_duration_seconds.observe(float(row[0]))

        for row in _safe_query(
            con,
            "select metric_value from monitoring.system_metrics "
            "where metric_category = 'streaming' and metric_name = 'streaming_lag_seconds' "
            "order by computed_at desc limit 1",
        ):
            registry.streaming_lag_seconds.set(float(row[0]))

        # ML inference duration: elt_model_runs rows orchestration/
        # ml_inference_flow.py's _append_inference_run() now writes
        # (load_strategy='ml_inference' - see that module's Phase 7 addition).
        for model_name, started_at, completed_at in _safe_query(
            con,
            "select model_name, started_at, completed_at from elt_model_runs "
            "where load_strategy = 'ml_inference' and status = 'success' "
            "order by completed_at desc limit 20",
        ):
            if started_at is None or completed_at is None:
                continue
            try:
                duration = (completed_at - started_at).total_seconds()
            except Exception:
                continue
            if duration >= 0:
                registry.ml_inference_duration_seconds.observe(duration, labels={"model": str(model_name)})

        # Anomaly frequency: total count, and per anomaly_type.
        for row in _safe_query(con, "select count(*) from anomalies.anomaly_events"):
            registry.anomaly_frequency_total.set_total(float(row[0]))
        for anomaly_type, count in _safe_query(
            con, "select anomaly_type, count(*) from anomalies.anomaly_events group by anomaly_type"
        ):
            registry.anomaly_frequency_total.set_total(float(count), labels={"anomaly_type": str(anomaly_type)})

        # Tenant usage: marts.compute_tenant_health (compute/polars/tenant_metrics.py, Phase 7 Section 2).
        for tenant_id, gmv in _safe_query(con, "select tenant_id, gmv from marts.compute_tenant_health"):
            registry.tenant_usage_gmv.set(float(gmv) if gmv is not None else 0.0, labels={"tenant_id": str(tenant_id)})

    return registry


# ---------------------------------------------------------------------------
# FastAPI middleware - imports fastapi/starlette lazily (see module
# docstring) so the rest of this file stays importable/testable without them.
# ---------------------------------------------------------------------------


def make_api_metrics_middleware(registry: MiniFaireRegistry | None = None):
    """Returns a `BaseHTTPMiddleware` subclass bound to `registry`
    (`app.add_middleware(make_api_metrics_middleware())` in
    api/metrics_api.py, alongside `auth/auth_middleware.py`'s
    RateLimitMiddleware) - a factory function rather than a plain class
    because the class body needs `BaseHTTPMiddleware` as a base, and that
    import has to stay inside this function for the module to remain
    importable without fastapi/starlette installed."""
    from starlette.middleware.base import BaseHTTPMiddleware

    active_registry = registry or default_registry()

    class ApiMetricsMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            route_label = request.url.path
            t0 = time.monotonic()
            try:
                response = await call_next(request)
            except Exception:
                active_registry.api_request_duration_seconds.observe(
                    time.monotonic() - t0, labels={"route": route_label, "method": request.method}
                )
                active_registry.api_errors_total.inc(labels={"route": route_label, "status": "500"})
                raise
            active_registry.api_request_duration_seconds.observe(
                time.monotonic() - t0, labels={"route": route_label, "method": request.method}
            )
            if response.status_code >= 500:
                active_registry.api_errors_total.inc(labels={"route": route_label, "status": str(response.status_code)})
            return response

    return ApiMetricsMiddleware


if __name__ == "__main__":
    refresh_from_warehouse()
    body, content_type = render_metrics_response()
    print(f"content-type: {content_type}\n{body}")
