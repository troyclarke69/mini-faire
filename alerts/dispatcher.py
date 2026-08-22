"""Alerting system (PHASE5-MONITORING.md Section 2).

Every anomaly, monitoring threshold breach, schema drift event, and pipeline
failure across this repo's Phase 5 additions funnels through one function -
`dispatch_alert()` - so there is exactly one place that decides where an
alert goes and exactly one place its history is recorded. Callers (
`anomalies/detector.py`, `monitoring/metrics.py`, `monitoring/schema_drift.py`,
`orchestration/realtime_flow.py`) never talk to Slack/webhooks directly.

Every dispatched alert is first persisted to `monitoring.alert_events`
(DuckDB) *before* any channel is attempted, so a Slack outage or a bad
webhook URL never loses the alert - it's already in the table and visible
via `api/monitoring_api.py`'s `/monitoring/alerts` and the frontend's Alerts
Feed regardless of whether any channel actually delivered it. Each channel
attempt's outcome is recorded back onto the persisted row.

Channels:

- Slack: a Slack "Incoming Webhook" URL, read from the environment variable
  named in `config/alerts.yaml`'s `channels.slack.webhook_env_var`
  (`SLACK_WEBHOOK_URL` by default). Never hardcoded, never stored in the
  config file - identical convention to `config/mongo.yaml`'s
  MONGO_PASSWORD handling.
- Generic webhook: any HTTP endpoint that accepts a JSON POST, read from
  `channels.webhook.webhook_env_var` (`ALERT_WEBHOOK_URL` by default).
- Console: always available, no configuration needed - the guaranteed
  fallback so an alert is never silently lost.

Both HTTP channels are best-effort: a network error, timeout, or non-2xx
response is caught, recorded, and logged - it never raises out of
`dispatch_alert()`. A monitoring subsystem crashing the pipeline it's
supposed to be monitoring would defeat the entire point of Phase 5.

Only the Python standard library is used for the HTTP POST (`urllib.request`)
rather than adding `requests`/`httpx` as a new dependency - this repo already
follows a minimal-dependency philosophy (see pyproject.toml's comments on
pytz/pyyaml/websockets each being added only because something concretely
needed them).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DUCKDB_PATH, PROJECT_ROOT

ALERTS_CONFIG_PATH = PROJECT_ROOT / "config" / "alerts.yaml"

# The nine alert types PHASE5-MONITORING.md Section 2 requires, plus the two
# PHASE6-ML.md Section 7 adds (ml_training_failure - orchestration/
# ml_training_flow.py's post-activation sanity check failed and rollback was
# attempted; ml_inference_failure - orchestration/ml_inference_flow.py's
# per-model-type inference pass raised). Every caller in this repo passes one
# of these; dispatch_alert() does not enforce membership (a future caller may
# need a new type without a code change here), but this tuple documents the
# contract and is what config/alerts.yaml's
# alert_type_severity/alert_type_dashboard_path key off.
ALERT_TYPES = (
    "anomaly_detected",
    "ingestion_failure",
    "ingestion_latency_threshold_exceeded",
    "elt_failure",
    "compute_failure",
    "schema_drift_detected",
    "quarantine_rate_spike",
    "mongo_change_stream_disconnect",
    "synthetic_generator_failure",
    "ml_training_failure",
    "ml_inference_failure",
)

DEFAULT_SEVERITY = "warning"
HTTP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class AlertsConfig:
    enabled: bool
    slack_enabled: bool
    slack_webhook_env_var: str
    webhook_enabled: bool
    webhook_env_var: str
    console_enabled: bool
    minimum_severity: str
    severity_order: list[str]
    frontend_base_url: str
    alert_type_severity: dict[str, str]
    alert_type_dashboard_path: dict[str, str]
    thresholds: dict[str, float]


def load_alerts_config(path: Path = ALERTS_CONFIG_PATH) -> AlertsConfig:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    channels = raw.get("channels") or {}
    slack = channels.get("slack") or {}
    webhook = channels.get("webhook") or {}
    console = channels.get("console") or {}
    return AlertsConfig(
        enabled=bool(raw.get("enabled", True)),
        slack_enabled=bool(slack.get("enabled", True)),
        slack_webhook_env_var=slack.get("webhook_env_var", "SLACK_WEBHOOK_URL"),
        webhook_enabled=bool(webhook.get("enabled", True)),
        webhook_env_var=webhook.get("webhook_env_var", "ALERT_WEBHOOK_URL"),
        console_enabled=bool(console.get("enabled", True)),
        minimum_severity=raw.get("minimum_severity", "info"),
        severity_order=list(raw.get("severity_order") or ["info", "warning", "critical"]),
        frontend_base_url=os.environ.get(
            "MONITORING_FRONTEND_URL", raw.get("frontend_base_url", "http://127.0.0.1:3000")
        ),
        alert_type_severity=dict(raw.get("alert_type_severity") or {}),
        alert_type_dashboard_path=dict(raw.get("alert_type_dashboard_path") or {}),
        thresholds={key: float(value) for key, value in (raw.get("thresholds") or {}).items()},
    )


@dataclass(frozen=True)
class Alert:
    alert_id: str
    alert_type: str
    severity: str
    entity: str
    message: str
    metadata: dict[str, Any]
    lineage_ref: str | None
    dashboard_url: str
    created_at: str


def _severity_rank(severity: str, order: list[str]) -> int:
    try:
        return order.index(severity)
    except ValueError:
        return 0  # unknown severity treated as lowest, so it's never suppressed by mistake


def _meets_minimum(severity: str, minimum: str, order: list[str]) -> bool:
    return _severity_rank(severity, order) >= _severity_rank(minimum, order)


def _alert_id(alert_type: str, entity: str) -> str:
    compact = utc_now().replace(":", "").replace("-", "").replace(".", "").replace("+", "z")
    safe_entity = "".join(ch if ch.isalnum() else "_" for ch in str(entity))[:40]
    return f"alert_{alert_type}_{safe_entity}_{compact}_{uuid.uuid4().hex[:8]}"


def build_alert(
    alert_type: str,
    *,
    entity: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    severity: str | None = None,
    lineage_ref: str | None = None,
    config: AlertsConfig | None = None,
) -> Alert:
    config = config or load_alerts_config()
    resolved_severity = severity or config.alert_type_severity.get(alert_type, DEFAULT_SEVERITY)
    dashboard_path = config.alert_type_dashboard_path.get(alert_type, "/monitoring")
    return Alert(
        alert_id=_alert_id(alert_type, entity),
        alert_type=alert_type,
        severity=resolved_severity,
        entity=str(entity),
        message=message,
        metadata=metadata or {},
        lineage_ref=lineage_ref,
        dashboard_url=f"{config.frontend_base_url.rstrip('/')}{dashboard_path}",
        created_at=utc_now(),
    )


def _post_json(url: str, payload: dict[str, Any]) -> tuple[bool, str]:
    body = json.dumps(payload, default=str).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300, f"http_{response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"http_error_{exc.code}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return False, f"connection_error: {exc!r}"


def _send_slack(alert: Alert, config: AlertsConfig) -> tuple[bool, str]:
    webhook_url = os.environ.get(config.slack_webhook_env_var)
    if not webhook_url:
        return False, "not_configured"
    text = f"[{alert.severity.upper()}] {alert.alert_type} - {alert.entity}: {alert.message}\n{alert.dashboard_url}"
    return _post_json(webhook_url, {"text": text})


def _send_webhook(alert: Alert, config: AlertsConfig) -> tuple[bool, str]:
    webhook_url = os.environ.get(config.webhook_env_var)
    if not webhook_url:
        return False, "not_configured"
    return _post_json(webhook_url, asdict(alert))


def _send_console(alert: Alert) -> tuple[bool, str]:
    print(f"  [ALERT][{alert.severity.upper()}] {alert.alert_type} - {alert.entity}: {alert.message}")
    return True, "printed"


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists monitoring")
    con.execute(
        """
        create table if not exists monitoring.alert_events (
          alert_id varchar primary key,
          alert_type varchar,
          severity varchar,
          entity varchar,
          message varchar,
          metadata varchar,
          lineage_ref varchar,
          dashboard_url varchar,
          created_at timestamptz,
          dispatched_channels varchar
        )
        """
    )


def persist_alert(alert: Alert, dispatch_results: dict[str, tuple[bool, str]], db_path: Path = DUCKDB_PATH) -> None:
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.execute(
            """
            insert or replace into monitoring.alert_events
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                alert.alert_id,
                alert.alert_type,
                alert.severity,
                alert.entity,
                alert.message,
                json.dumps(alert.metadata, default=str, sort_keys=True),
                alert.lineage_ref,
                alert.dashboard_url,
                alert.created_at,
                json.dumps(
                    {channel: {"ok": ok, "detail": detail} for channel, (ok, detail) in dispatch_results.items()},
                    sort_keys=True,
                ),
            ],
        )
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=alert.alert_id,
                source_node=alert.lineage_ref or f"alerts://{alert.alert_type}",
                target_node="monitoring.alert_events",
                edge_type="alert_dispatched",
                entity=alert.entity,
                created_at=alert.created_at,
            )
        ]
    )


def dispatch_alert(
    alert_type: str,
    *,
    entity: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    severity: str | None = None,
    lineage_ref: str | None = None,
    config: AlertsConfig | None = None,
    db_path: Path = DUCKDB_PATH,
) -> Alert:
    """Build, persist, and (if enabled and above the configured minimum
    severity) send an alert through every enabled channel. Always persists
    first - a channel failure never causes the alert to go unrecorded - and
    never raises: callers (anomaly detection, monitoring, real-time
    orchestration) call this from paths that must keep running even if
    alerting itself is misconfigured or unreachable."""
    config = config or load_alerts_config()
    alert = build_alert(
        alert_type, entity=entity, message=message, metadata=metadata,
        severity=severity, lineage_ref=lineage_ref, config=config,
    )

    results: dict[str, tuple[bool, str]] = {}
    try:
        if not config.enabled:
            results["_skipped"] = (False, "alerts_disabled")
        elif not _meets_minimum(alert.severity, config.minimum_severity, config.severity_order):
            results["_skipped"] = (False, f"below_minimum_severity({config.minimum_severity})")
        else:
            if config.slack_enabled:
                results["slack"] = _send_slack(alert, config)
            if config.webhook_enabled:
                results["webhook"] = _send_webhook(alert, config)
            if config.console_enabled:
                results["console"] = _send_console(alert)
    except Exception as exc:  # noqa: BLE001 - dispatching must never crash the caller
        print(f"  alert dispatch encountered an unexpected error ({exc!r}); alert is still persisted.")
        results["_error"] = (False, repr(exc))

    try:
        persist_alert(alert, results, db_path)
    except Exception as exc:  # noqa: BLE001 - see module docstring: never raise out of here
        print(f"  could not persist alert {alert.alert_id} ({exc!r})")

    return alert


if __name__ == "__main__":
    demo = dispatch_alert(
        "anomaly_detected",
        entity="demo",
        message="Manual test alert from `python -m alerts.dispatcher`.",
        severity="info",
    )
    print(f"Dispatched {demo.alert_id}")
