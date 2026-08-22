"""Observability: structured logging (PHASE7-DEPLOYMENT.md Section 8).

`configure_json_logging(service_name)` points Python's stdlib `logging` at a
JSON formatter writing to stdout - the standard way a containerized service
gets its logs into Loki without adding a Loki client dependency: Loki
ingests via Promtail (or Docker's own logging driver) scraping container
stdout, not via the application pushing to Loki's HTTP API directly.
`docker-compose.observability.yaml`'s `promtail` service is configured to
scrape exactly this JSON shape (one JSON object per line: `timestamp`,
`level`, `service`, `message`, plus whatever `extra=` fields a call site
adds) from every other service's container logs.

`push_to_loki()` is the direct-push alternative for the one case that needs
it - somewhere NOT running inside this repo's docker-compose (Fly.io/Render
processes, whose logs come from platform log capture that isn't Promtail) -
hand-rolled with `urllib.request` rather than a `python-logging-loki`
dependency, the same "hand-roll a small HTTP POST rather than add a package"
call `alerts/dispatcher.py`'s webhook dispatch already made. Neither
function is wired into every module automatically - `configure_json_logging()`
is meant to be called once, at process startup, by whichever entry point
this repo already has (api/metrics_api.py, each orchestration/*.py flow's
`if __name__ == "__main__":`), same as how logging setup normally works.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Anything passed via logging's `extra={...}` kwarg lands as plain
        # attributes on the record - LogRecord's own built-in attribute
        # names are excluded so a caller's extra fields never collide with
        # (or get shadowed by) timestamp/level/service/logger/message above.
        reserved = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}
        for key, value in record.__dict__.items():
            if key not in reserved and key not in payload:
                try:
                    json.dumps(value)  # skip anything that wouldn't survive round-tripping
                    payload[key] = value
                except TypeError:
                    payload[key] = repr(value)
        return json.dumps(payload, default=str, sort_keys=True)


def configure_json_logging(service_name: str, *, level: int = logging.INFO) -> logging.Logger:
    """Idempotent - safe to call more than once (e.g. a module imported by
    both a test and the real entry point); only the first call actually
    attaches a handler, matching `observability/tracing.py`'s
    `init_tracing()` "safe to call more than once" contract."""
    root = logging.getLogger()
    already_configured = any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)
    if not already_configured:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(JsonFormatter(service_name))
        root.addHandler(handler)
    root.setLevel(level)
    return logging.getLogger(service_name)


def push_to_loki(
    *, loki_url: str, service_name: str, level: str, message: str,
    labels: dict[str, str] | None = None, timeout: float = 3.0,
) -> None:
    """Direct push to Loki's HTTP push API (`POST /loki/api/v1/push`) - see
    module docstring for when this is the right call vs. the default
    stdout-JSON-plus-Promtail path. Silently logs (to stderr, not back
    through this same logging setup - avoiding a push-failure-logs-a-push-
    failure loop) and returns rather than raising, matching
    `alerts/dispatcher.py`'s "a notification failure must never take down
    the caller" posture."""
    stream_labels = {"service": service_name, "level": level, **(labels or {})}
    timestamp_ns = str(int(time.time() * 1_000_000_000))
    body = json.dumps({"streams": [{"stream": stream_labels, "values": [[timestamp_ns, message]]}]}).encode("utf-8")
    request = urllib.request.Request(
        f"{loki_url.rstrip('/')}/loki/api/v1/push", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"observability.logging: push_to_loki failed: {exc!r}", file=sys.stderr)
