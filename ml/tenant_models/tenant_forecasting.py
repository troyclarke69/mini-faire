"""Tenant-aware ML (PHASE7-DEPLOYMENT.md Section 2).

One concrete tenant-scoped model: per-tenant GMV forecasting over
`warehouse/duckdb/tenant_elt.sql`'s `marts.metrics_tenant_daily` - the same
"orders is the one entity carried end-to-end" scoping decision
`ingestion/tenant_ingest.py`'s module docstring explains for the rest of
this phase's tenant-aware pipeline.

Deliberately a thin wrapper around `ml/models/forecasting.py`'s existing
per-series forecaster (`forecast_series()`, `_rows_for_series()`, the
`Forecast` dataclass, `persist_forecasts()`) rather than a second forecasting
implementation - a tenant's daily GMV series is fit exactly the same way the
marketplace-wide GMV series is (Holt-Winters -> RandomForest -> linear-trend
fallback), just against `marts.metrics_tenant_daily where tenant_id = ?`
instead of `marts.metrics_retailer_daily`. `ml/registry.py` needs no changes
either - a tenant's forecasting model is registered under its own
`model_name` (`f"tenant_{tenant_id}_forecasting"`), so it versions,
activates, and rolls back completely independently of every other tenant's
model and of the marketplace-wide `"forecasting"` model_name, using exactly
the registry functions every other `ml/models/*.py` module already calls.

Scope note: this module trains one forecast type (tenant daily GMV, entity
type `"tenant"`, entity_id the tenant_id) per tenant, not the full
velocity/inventory/price catalog `ml/models/forecasting.py` covers for the
marketplace-wide case - a tenant's raw ingestion only carries `orders`
end-to-end (see `ingestion/tenant_ingest.py`), so there is no tenant-scoped
inventory/price series to forecast yet."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ingestion.duckdb_utils import connect_with_retry
from ingestion.paths import DUCKDB_PATH
from ml.config import MLConfig, load_ml_config
from ml.models.forecasting import Forecast, _rows_for_series, forecast_series, persist_forecasts
from ml.registry import ModelRecord, activate_model, get_active_model, register_model

TENANT_FORECASTING_MODEL_TYPE = "tenant_forecasting"


def tenant_forecasting_model_name(tenant_id: str) -> str:
    return f"tenant_{tenant_id}_forecasting"


def _tenant_gmv_series(con, tenant_id: str) -> list[float]:
    rows = con.execute(
        "select order_date, sum(gmv) from marts.metrics_tenant_daily "
        "where tenant_id = ? group by order_date order by order_date",
        [tenant_id],
    ).fetchall()
    return [float(value) if value is not None else 0.0 for (_day, value) in rows]


def forecast_tenant_gmv(
    tenant_id: str, con, *, config: MLConfig, model_version: int | None = None
) -> list[Forecast]:
    series = _tenant_gmv_series(con, tenant_id)
    return _rows_for_series(
        forecast_type="tenant_gmv_daily", entity_type="tenant", entity_id=tenant_id,
        series=series, horizon_days=config.forecast_horizon_days, n_lags=config.forecast_n_lags,
        model_version=model_version,
    )


def run_tenant_forecasts(
    tenant_id: str, db_path: Path = DUCKDB_PATH, *, config: MLConfig | None = None, model_version: int | None = None
) -> list[Forecast]:
    if not db_path.exists():
        return []
    config = config or load_ml_config()
    with connect_with_retry(db_path, read_only=True) as con:
        try:
            return forecast_tenant_gmv(tenant_id, con, config=config, model_version=model_version)
        except Exception as exc:  # noqa: BLE001 - one tenant failing shouldn't block the rest of a training run
            print(f"  tenant forecast for {tenant_id!r} failed: {exc!r}")
            return []


def evaluate_tenant_forecasting(
    tenant_id: str, db_path: Path = DUCKDB_PATH, *, config: MLConfig | None = None
) -> dict[str, Any]:
    """Backtest MAE for one tenant's GMV series - the tenant-scoped
    counterpart of `ml/models/forecasting.py`'s `evaluate_forecasting()`,
    used the same way (promotion gating in `train_and_register_tenant_model`
    below). Returns `{"mae": None, ...}` when there isn't enough history to
    hold out a backtest window yet (e.g. a tenant that just onboarded) -
    callers must treat that as "can't evaluate yet", not a score of zero."""
    if not db_path.exists():
        return {"mae": None, "series_evaluated": 0}
    config = config or load_ml_config()
    with connect_with_retry(db_path, read_only=True) as con:
        series = _tenant_gmv_series(con, tenant_id)

    holdout = min(config.forecast_horizon_days, max(1, len(series) // 5))
    if len(series) < holdout + 5:
        return {"mae": None, "series_evaluated": 0}
    train, actual = series[:-holdout], series[-holdout:]
    point, _lower, _upper, _method = forecast_series(train, holdout, n_lags=config.forecast_n_lags)
    errors = [abs(p - a) for p, a in zip(point, actual)]
    return {"mae": sum(errors) / len(errors), "series_evaluated": 1}


def train_and_register_tenant_model(
    tenant_id: str, db_path: Path = DUCKDB_PATH, *, config: MLConfig | None = None
) -> ModelRecord:
    """The tenant-scoped counterpart of
    `orchestration/ml_training_flow.py`'s `_train_forecasting()`: evaluate,
    register (inactive), promote only if it's at least as good as the
    tenant's currently-active model (or there isn't one yet), then run
    inference and persist. No estimator is pickled (matching
    `ml/models/forecasting.py` - `forecast_series()` refits per call, there
    is no single fitted object to save), same as the marketplace-wide
    forecasting model_name."""
    config = config or load_ml_config()
    model_name = tenant_forecasting_model_name(tenant_id)

    metrics = evaluate_tenant_forecasting(tenant_id, db_path, config=config)
    active = get_active_model(model_name, db_path)
    record = register_model(
        model_name, TENANT_FORECASTING_MODEL_TYPE,
        params={"tenant_id": tenant_id, "horizon_days": config.forecast_horizon_days, "n_lags": config.forecast_n_lags},
        metrics=metrics, estimator=None, activate=False, db_path=db_path,
    )

    active_mae = active.metrics.get("mae") if active else None
    new_mae = metrics.get("mae")
    # No prior model, or the new model actually has a score (an
    # unevaluatable model - not enough history yet - is never promoted over
    # a model that does have a score) and it isn't worse than the
    # incumbent's: lower MAE is better, matching
    # ml_training_flow.py's "forecasting": ("mae", "lower_better") direction.
    promote = active is None or (new_mae is not None and (active_mae is None or new_mae <= active_mae))
    if not promote:
        return record

    activate_model(model_name, record.version, db_path)
    forecasts = run_tenant_forecasts(tenant_id, db_path, config=config, model_version=record.version)
    persist_forecasts(forecasts, db_path)
    return record


def train_and_register_all_tenants(db_path: Path = DUCKDB_PATH, *, config: MLConfig | None = None) -> list[ModelRecord]:
    from multi_tenant.tenant_manager import STATUS_ACTIVE, list_tenants

    config = config or load_ml_config()
    records: list[ModelRecord] = []
    for tenant in list_tenants(status=STATUS_ACTIVE, db_path=db_path):
        try:
            records.append(train_and_register_tenant_model(tenant.tenant_id, db_path, config=config))
        except Exception as exc:  # noqa: BLE001 - one tenant's model failing shouldn't block the rest
            print(f"  tenant model training for {tenant.tenant_id!r} failed: {exc!r}")
    return records


if __name__ == "__main__":
    results = train_and_register_all_tenants()
    print(f"Trained/registered {len(results)} tenant forecasting models")
