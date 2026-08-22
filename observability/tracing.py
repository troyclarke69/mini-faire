"""Observability: distributed tracing (PHASE7-DEPLOYMENT.md Section 8).

`start_span(name)` is the one entry point every instrumented module calls -
`with start_span("ingest_tenant_file"): ...` - regardless of whether
OpenTelemetry is installed. Two implementations behind that same API:

- When the `[observability]` extra (pyproject.toml) is installed,
  `init_tracing()` configures a real OpenTelemetry `TracerProvider` exporting
  spans via OTLP to Jaeger (the `docker-compose.observability.yaml` service
  this repo's Phase 7 addition runs alongside Prometheus/Grafana/Loki - see
  that file), and `start_span()` returns a real OpenTelemetry span.
- When it isn't installed (this sandbox has no network access to the
  package index - see pyproject.toml's `observability` extra), `start_span()`
  falls back to `NoOpSpan`, a context manager with the same
  `set_attribute()`/`record_exception()` surface that does nothing. Callers
  never need to check which mode they're in - unlike
  `ml/models/forecasting.py`'s statsmodels fallback (a materially different
  algorithm when the optional package is missing), tracing degrading to "no
  spans emitted" changes nothing about program behavior or correctness, only
  observability - so one function with an internal branch, not two APIs
  callers have to choose between, is the right shape here.

This module deliberately does NOT auto-instrument every function in this
repo - PHASE7-DEPLOYMENT.md Section 8's "track ingestion latency / ELT
duration / compute duration / ML inference duration" is satisfied by
`observability/metrics.py`'s `refresh_from_warehouse()` reading already-
collected durations (see that module's docstring), which needed no call-site
changes anywhere. `start_span()` is for the orthogonal need distributed
tracing actually serves - following one request/run across process/service
boundaries (e.g. an API request into the backend, out to a Postgres/Mongo
call, and results back) - so it's wired into the two places that shape
matters for this repo: `api/metrics_api.py`'s request middleware
(`TracingMiddleware` below) and `database/cloud_db.py`'s
`PostgresConnectionManager`/`MongoConnectionManager` connection methods,
rather than sprinkled through every function in every module.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

_tracer = None
_otel_available = False

try:
    from opentelemetry import trace as _otel_trace  # noqa: F401

    _otel_available = True
except ImportError:
    _otel_available = False


class NoOpSpan:
    """Returned by `start_span()` when OpenTelemetry isn't installed - same
    surface as a real span's most-used methods, does nothing with any of
    them. `__bool__` is not overridden (a NoOpSpan is always truthy), so
    `with start_span(...) as span: span.set_attribute(...)` never needs an
    `if span:` guard at the call site."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass


def is_tracing_enabled() -> bool:
    return _tracer is not None


def init_tracing(service_name: str, *, otlp_endpoint: str | None = None) -> bool:
    """Configures a real OpenTelemetry TracerProvider exporting to
    `otlp_endpoint` (defaults to the OTEL_EXPORTER_OTLP_ENDPOINT env var, or
    `http://localhost:4317` - Jaeger's OTLP gRPC port in
    docker-compose.observability.yaml) if the `[observability]` extra is
    installed. Returns True if tracing is now active, False if it fell back
    to no-op (package not installed) - callers (api/metrics_api.py's
    startup) can log which mode they're in but never need to branch on it
    for correctness. Safe to call more than once; only the first call
    actually configures a provider."""
    global _tracer
    if not _otel_available:
        return False
    if _tracer is not None:
        return True

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)
    return True


@contextmanager
def start_span(name: str, *, attributes: dict[str, Any] | None = None):
    """`with start_span("ingest_tenant_file", attributes={"tenant_id": tenant_id}): ...`
    Always usable regardless of whether `init_tracing()` was ever called or
    succeeded - falls back to `NoOpSpan` in both cases (not initialized, or
    OpenTelemetry not installed)."""
    if _tracer is None:
        yield NoOpSpan()
        return

    with _tracer.start_as_current_span(name) as span:
        for key, value in (attributes or {}).items():
            span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            raise


def make_tracing_middleware():
    """Returns a `BaseHTTPMiddleware` subclass (imports fastapi/starlette
    lazily - see observability/metrics.py's `make_api_metrics_middleware()`
    for why) that opens one span per HTTP request, named after the route
    path, with the response status code and method as attributes."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class TracingMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            with start_span(f"http {request.method} {request.url.path}") as span:
                span.set_attribute("http.method", request.method)
                span.set_attribute("http.route", request.url.path)
                response = await call_next(request)
                span.set_attribute("http.status_code", response.status_code)
                return response

    return TracingMiddleware
