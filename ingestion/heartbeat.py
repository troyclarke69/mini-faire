"""Lightweight filesystem heartbeat for the Phase 4 streaming services
(synthetic/stream_generator.py, ingestion/mongo_change_stream.py,
orchestration/realtime_flow.py) so api/realtime_api.py's /realtime/health can
report whether each is actually running. These run as separate OS processes
(see PHASE4-REALTIME&STREAMING.md's three `python ...` commands), so there is
no in-process object the API server could ask directly.

Each service periodically overwrites its own small JSON file under
data/state/. The API considers a service:

- "running" if its heartbeat file exists and was written within a freshness
  window (default 30s - generous relative to every service's own polling
  cadence, which is at most a couple of seconds),
- "stale" if it exists but is older than that (the process likely died or
  was killed without a clean shutdown),
- "not_running" if the file has never been written.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ingestion.paths import STATE_DIR


def heartbeat_path(service: str) -> Path:
    return STATE_DIR / f"_{service}_heartbeat.json"


def write_heartbeat(service: str, payload: dict[str, Any] | None = None) -> None:
    path = heartbeat_path(service)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"service": service, "written_at": datetime.now(UTC).isoformat(), **(payload or {})}
    path.write_text(json.dumps(body, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def read_heartbeat(service: str) -> dict[str, Any] | None:
    path = heartbeat_path(service)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def heartbeat_status(service: str, fresh_within_seconds: float = 30.0) -> dict[str, Any]:
    body = read_heartbeat(service)
    if body is None:
        return {"service": service, "status": "not_running", "last_heartbeat_at": None}
    written_at = body.get("written_at")
    status = "stale"
    if written_at:
        try:
            parsed = datetime.fromisoformat(str(written_at).replace("Z", "+00:00"))
            age = (datetime.now(UTC) - parsed).total_seconds()
            status = "running" if age <= fresh_within_seconds else "stale"
        except ValueError:
            status = "stale"
    return {"service": service, "status": status, "last_heartbeat_at": written_at, "detail": body}
