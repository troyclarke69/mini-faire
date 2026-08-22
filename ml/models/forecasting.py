"""Forecasting Models (PHASE6-ML.md Section 3).

One registry model_name, `"forecasting"` (see FORECASTING_MODEL_NAME), covers
every forecast type below - GMV (daily/weekly/retailer-level), velocity
(product/retailer), inventory, and price all share the same underlying
per-series forecasting method (`forecast_series()`) rather than each being a
separately-versioned, separately-evaluated registry entry. This is a
deliberate simplification: PHASE6-ML.md Section 7 asks orchestration/
ml_training_flow.py to "train models, evaluate models, register models,
activate new models, rollback on failure" per Section 3-6 category, and
treating "forecasting" as one versioned unit (evaluated by a backtest MAE
sampled across a handful of representative series - see
`evaluate_forecasting()`) keeps that promotion/rollback machinery meaningful
without needing seven near-identical registry entries for what is, under the
hood, the exact same forecasting approach applied to different series.

`forecast_series()` is the actual per-series model, tried in this order:

1. statsmodels' Holt-Winters exponential smoothing (`ExponentialSmoothing`),
   if statsmodels is installed and the series is long enough for a damped
   trend fit to be meaningful. Optional - see pyproject.toml's [ml-extra].
2. scikit-learn's `RandomForestRegressor` over lag features (yesterday, 2
   days ago, ... `n_lags` days ago), predicting recursively (each predicted
   step becomes a new lag for the next step). This is the module's
   documented default - PHASE6-ML.md explicitly suggests RandomForestRegressor
   among its "such as" list, and it needs no dependency beyond this repo's
   [ml] extra (numpy + scikit-learn).
3. A numpy-only linear trend extrapolation (`np.polyfit` degree 1), used
   when there isn't enough history for a lag-feature model (fewer than 5
   training examples - e.g. a product with under two weeks of data). Always
   available since numpy is a hard dependency of this module.

Confidence bounds are an empirical approximation in every case - the
residual standard deviation of the fitted model against its own training
data, applied as forecast_value +/- 1.28*residual_std (~80% band under a
normality assumption). This is a clearly-labeled proxy, not a rigorous
prediction interval - the same "proxy, not the real thing" philosophy
monitoring/metrics.py uses for its streaming backlog/lag metrics.

Every forecast type produces one row per day of the horizon (not a single
summary row), so `api/ml_api.py` / the frontend's ForecastChart can plot a
full forecast curve. Inventory forecasts (`inventory_level`) fold PHASE6-ML.md's
"stockout prediction" and "reorder prediction" into per-day boolean flags in
each row's metadata (`below_stockout_threshold` / `below_reorder_threshold`)
rather than two more forecast_types - the underlying curve is the same
inventory projection either way, just evaluated against two thresholds.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from anomalies.detector import percentile
from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DUCKDB_PATH
from ml.config import MLConfig, load_ml_config

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "ml/models/forecasting.py requires numpy. Install this repo's ML "
        'extra with `pip install -e ".[ml]"` (see pyproject.toml).'
    ) from exc

try:
    from sklearn.ensemble import RandomForestRegressor
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "ml/models/forecasting.py requires scikit-learn. Install this "
        'repo\'s ML extra with `pip install -e ".[ml]"` (see pyproject.toml).'
    ) from exc

try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
except ImportError:  # optional enhancement - see pyproject.toml's [ml-extra]
    ExponentialSmoothing = None

FORECASTING_MODEL_NAME = "forecasting"
CONFIDENCE_Z = 1.28  # ~80% band under a normality assumption on residuals


# ---------------------------------------------------------------------------
# Core per-series forecaster
# ---------------------------------------------------------------------------


def _lag_matrix(series: list[float], n_lags: int) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(series, dtype=float)
    if len(values) <= n_lags:
        return np.empty((0, n_lags)), np.empty((0,))
    X = np.array([values[i:i + n_lags] for i in range(len(values) - n_lags)])
    y = values[n_lags:]
    return X, y


def forecast_series(series: list[float], horizon: int, *, n_lags: int = 7) -> tuple[list[float], list[float], list[float], str]:
    """Recursive multi-step forecast. Returns (point_forecasts, lower_bounds,
    upper_bounds, method) - see module docstring for the three methods tried
    in order."""
    values = np.asarray(series, dtype=float)
    n = len(values)

    if ExponentialSmoothing is not None and n >= max(10, n_lags * 2):
        try:
            fitted = ExponentialSmoothing(values, trend="add", damped_trend=True).fit()
            point = np.asarray(fitted.forecast(horizon), dtype=float)
            residual_std = float(np.std(fitted.fittedvalues - values))
            lower = point - CONFIDENCE_Z * residual_std
            upper = point + CONFIDENCE_Z * residual_std
            return point.tolist(), lower.tolist(), upper.tolist(), "holt_winters"
        except Exception:
            pass  # fall through to the RandomForestRegressor default

    X, y = _lag_matrix(series, n_lags)
    if len(X) >= 5:
        model = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42)
        model.fit(X, y)
        residual_std = float(np.std(model.predict(X) - y))
        window = list(values[-n_lags:])
        point: list[float] = []
        for _ in range(horizon):
            pred = float(model.predict(np.asarray(window[-n_lags:], dtype=float).reshape(1, -1))[0])
            point.append(pred)
            window.append(pred)
        point_arr = np.asarray(point)
        lower = point_arr - CONFIDENCE_Z * residual_std
        upper = point_arr + CONFIDENCE_Z * residual_std
        return point_arr.tolist(), lower.tolist(), upper.tolist(), "random_forest"

    # Linear trend fallback - always available (numpy only), used when there
    # isn't enough history for a lag-feature model.
    x_idx = np.arange(n)
    if n >= 2:
        slope, intercept = np.polyfit(x_idx, values, 1)
        residual_std = float(np.std(values - (slope * x_idx + intercept)))
    elif n == 1:
        slope, intercept, residual_std = 0.0, float(values[0]), 0.0
    else:
        slope, intercept, residual_std = 0.0, 0.0, 0.0
    future_idx = np.arange(n, n + horizon)
    point_arr = slope * future_idx + intercept
    lower = point_arr - CONFIDENCE_Z * residual_std
    upper = point_arr + CONFIDENCE_Z * residual_std
    return point_arr.tolist(), lower.tolist(), upper.tolist(), "linear_trend"


# ---------------------------------------------------------------------------
# Forecast record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Forecast:
    forecast_id: str
    forecast_type: str
    entity_type: str
    entity_id: str
    target_date: str
    forecast_value: float
    lower_bound: float
    upper_bound: float
    model_name: str
    model_version: int | None
    generated_at: str
    horizon_days: int
    metadata: dict[str, Any]


def _forecast_id(forecast_type: str, entity_id: str, target_date: str) -> str:
    # Deterministic (no uuid/timestamp component) - a forecast for a given
    # (type, entity, future date) is meant to be replaced by the next
    # inference pass' updated prediction, not accumulated as history, unlike
    # anomalies/alerts which intentionally keep every row.
    safe_entity = "".join(ch if ch.isalnum() else "_" for ch in str(entity_id))[:40]
    return f"forecast_{forecast_type}_{safe_entity}_{target_date}"


def _rows_for_series(
    *, forecast_type: str, entity_type: str, entity_id: str, series: list[float],
    horizon_days: int, n_lags: int, model_version: int | None,
    extra_metadata: Callable[[int, float], dict[str, Any]] | None = None,
) -> list[Forecast]:
    if len(series) < 2:
        return []
    point, lower, upper, method = forecast_series(series, horizon_days, n_lags=n_lags)
    generated_at = utc_now()
    today = datetime.now(UTC).date()
    rows: list[Forecast] = []
    for step, (p, lo, hi) in enumerate(zip(point, lower, upper), start=1):
        target_date = str(today + timedelta(days=step))
        metadata: dict[str, Any] = {"method": method}
        if extra_metadata:
            metadata.update(extra_metadata(step, p))
        rows.append(
            Forecast(
                forecast_id=_forecast_id(forecast_type, entity_id, target_date),
                forecast_type=forecast_type,
                entity_type=entity_type,
                entity_id=str(entity_id),
                target_date=target_date,
                forecast_value=float(p),
                lower_bound=float(lo),
                upper_bound=float(hi),
                model_name=FORECASTING_MODEL_NAME,
                model_version=model_version,
                generated_at=generated_at,
                horizon_days=horizon_days,
                metadata=metadata,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Dataset-specific series builders (DuckDB -> list[float])
# ---------------------------------------------------------------------------


def _series(con, sql: str, params: list[Any] | None = None) -> list[float]:
    rows = con.execute(sql, params) if params is not None else con.execute(sql)
    return [float(value) if value is not None else 0.0 for (_day, value) in rows.fetchall()]


def _top_retailers(con, top_n: int) -> list[str]:
    rows = con.execute(
        "select retailer_id from marts.metrics_retailer_daily "
        "group by retailer_id order by sum(gmv) desc limit ?",
        [top_n],
    ).fetchall()
    return [r[0] for r in rows]


def _top_products(con, top_n: int) -> list[str]:
    rows = con.execute(
        "select product_id from marts.metrics_product_velocity "
        "order by units_sold desc limit ?",
        [top_n],
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Forecast generation (inference)
# ---------------------------------------------------------------------------


def forecast_gmv(con, *, config: MLConfig, model_version: int | None = None) -> list[Forecast]:
    rows: list[Forecast] = []

    daily = _series(con, "select order_date, sum(gmv) from marts.metrics_retailer_daily group by order_date order by order_date")
    rows += _rows_for_series(
        forecast_type="gmv_daily", entity_type="system", entity_id="marketplace",
        series=daily, horizon_days=config.forecast_horizon_days, n_lags=config.forecast_n_lags,
        model_version=model_version,
    )

    weekly = _series(
        con,
        "select date_trunc('week', order_date) as wk, sum(gmv) from marts.metrics_retailer_daily "
        "group by wk order by wk",
    )
    weekly_horizon = max(1, config.forecast_horizon_days // 7)
    rows += _rows_for_series(
        forecast_type="gmv_weekly", entity_type="system", entity_id="marketplace",
        series=weekly, horizon_days=weekly_horizon, n_lags=min(config.forecast_n_lags, max(2, len(weekly) - 1)),
        model_version=model_version,
    )

    for retailer_id in _top_retailers(con, config.forecast_top_n_entities):
        series = _series(
            con, "select order_date, gmv from marts.metrics_retailer_daily where retailer_id = ? order by order_date",
            [retailer_id],
        )
        rows += _rows_for_series(
            forecast_type="gmv_retailer", entity_type="retailer", entity_id=retailer_id,
            series=series, horizon_days=config.forecast_horizon_days, n_lags=config.forecast_n_lags,
            model_version=model_version,
        )
    return rows


def forecast_velocity(con, *, config: MLConfig, model_version: int | None = None) -> list[Forecast]:
    rows: list[Forecast] = []

    for product_id in _top_products(con, config.forecast_top_n_entities):
        series = _series(
            con,
            "select order_date, count(distinct order_id) from marts.fact_orders where product_id = ? "
            "group by order_date order by order_date",
            [product_id],
        )
        rows += _rows_for_series(
            forecast_type="velocity_product", entity_type="product", entity_id=product_id,
            series=series, horizon_days=config.forecast_horizon_days, n_lags=config.forecast_n_lags,
            model_version=model_version,
        )

    for retailer_id in _top_retailers(con, config.forecast_top_n_entities):
        series = _series(
            con, "select order_date, order_count from marts.metrics_retailer_daily where retailer_id = ? order by order_date",
            [retailer_id],
        )
        rows += _rows_for_series(
            forecast_type="velocity_retailer", entity_type="retailer", entity_id=retailer_id,
            series=series, horizon_days=config.forecast_horizon_days, n_lags=config.forecast_n_lags,
            model_version=model_version,
        )
    return rows


def forecast_inventory(con, *, config: MLConfig, model_version: int | None = None) -> list[Forecast]:
    current_counts = [
        float(row[0]) for row in con.execute("select inventory_count from marts.dim_product where is_active").fetchall()
        if row[0] is not None
    ]
    reorder_threshold = percentile(current_counts, config.reorder_inventory_percentile) if current_counts else 0.0

    rows: list[Forecast] = []
    for product_id in _top_products(con, config.forecast_top_n_entities):
        series = _series(
            con,
            "select cast(event_ts as date) as day, arg_max(inventory_count_after, event_ts) from "
            "marts.fact_product_events where event_type = 'inventory_updated' and product_id = ? "
            "group by day order by day",
            [product_id],
        )
        rows += _rows_for_series(
            forecast_type="inventory_level", entity_type="product", entity_id=product_id,
            series=series, horizon_days=config.forecast_horizon_days, n_lags=config.forecast_n_lags,
            model_version=model_version,
            extra_metadata=lambda _step, value: {
                "below_stockout_threshold": value <= config.stockout_inventory_threshold,
                "below_reorder_threshold": value <= reorder_threshold,
                "reorder_threshold": reorder_threshold,
            },
        )
    return rows


def forecast_price(con, *, config: MLConfig, model_version: int | None = None) -> list[Forecast]:
    rows: list[Forecast] = []
    for product_id in _top_products(con, config.forecast_top_n_entities):
        series = _series(
            con,
            "select cast(event_ts as date) as day, arg_max(new_price, event_ts) from "
            "marts.fact_product_events where event_type = 'price_changed' and product_id = ? "
            "group by day order by day",
            [product_id],
        )
        rows += _rows_for_series(
            forecast_type="price_trend", entity_type="product", entity_id=product_id,
            series=series, horizon_days=config.forecast_horizon_days, n_lags=min(config.forecast_n_lags, max(2, len(series) - 1)),
            model_version=model_version,
        )
    return rows


def run_all_forecasts(db_path: Path = DUCKDB_PATH, *, config: MLConfig | None = None, model_version: int | None = None) -> list[Forecast]:
    if not db_path.exists():
        return []
    config = config or load_ml_config()
    forecasts: list[Forecast] = []
    with connect_with_retry(db_path, read_only=True) as con:
        for label, builder in (
            ("gmv", forecast_gmv), ("velocity", forecast_velocity),
            ("inventory", forecast_inventory), ("price", forecast_price),
        ):
            try:
                forecasts.extend(builder(con, config=config, model_version=model_version))
            except Exception as exc:  # noqa: BLE001 - one forecast category failing shouldn't block the rest
                print(f"  forecast group '{label}' failed: {exc!r}")
    return forecasts


# ---------------------------------------------------------------------------
# Backtest evaluation (for registry promotion gating - see
# orchestration/ml_training_flow.py)
# ---------------------------------------------------------------------------


def evaluate_forecasting(db_path: Path = DUCKDB_PATH, *, config: MLConfig | None = None) -> dict[str, Any]:
    """Holds out the tail of a handful of representative series (overall
    daily GMV, plus up to 3 top retailers/products each), forecasts that
    held-out span from the remaining history, and returns the mean absolute
    error against actuals. Lower is better - see
    orchestration/ml_training_flow.py's promotion-gating direction table.
    Returns {"mae": None, ...} if there isn't enough history anywhere to
    evaluate (e.g. a brand-new warehouse) - callers must treat that as
    "can't evaluate yet", not as a score of zero."""
    if not db_path.exists():
        return {"mae": None, "series_evaluated": 0}
    config = config or load_ml_config()

    errors: list[float] = []
    with connect_with_retry(db_path, read_only=True) as con:
        series_list: list[list[float]] = [
            _series(con, "select order_date, sum(gmv) from marts.metrics_retailer_daily group by order_date order by order_date")
        ]
        for retailer_id in _top_retailers(con, 3):
            series_list.append(_series(
                con, "select order_date, gmv from marts.metrics_retailer_daily where retailer_id = ? order by order_date",
                [retailer_id],
            ))
        for product_id in _top_products(con, 3):
            series_list.append(_series(
                con, "select order_date, count(distinct order_id) from marts.fact_orders where product_id = ? "
                "group by order_date order by order_date",
                [product_id],
            ))

    for series in series_list:
        holdout = min(config.forecast_horizon_days, max(1, len(series) // 5))
        if len(series) < holdout + 5:
            continue  # not enough history to both train and evaluate this series
        train, actual = series[:-holdout], series[-holdout:]
        point, _lower, _upper, _method = forecast_series(train, holdout, n_lags=config.forecast_n_lags)
        errors.extend(abs(p - a) for p, a in zip(point, actual))

    if not errors:
        return {"mae": None, "series_evaluated": 0}
    return {"mae": float(np.mean(errors)), "series_evaluated": len(series_list)}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists ml")
    con.execute(
        """
        create table if not exists ml.forecasts (
          forecast_id varchar primary key,
          forecast_type varchar,
          entity_type varchar,
          entity_id varchar,
          target_date date,
          forecast_value double,
          lower_bound double,
          upper_bound double,
          model_name varchar,
          model_version integer,
          generated_at timestamptz,
          horizon_days integer,
          metadata varchar
        )
        """
    )


def persist_forecasts(forecasts: list[Forecast], db_path: Path = DUCKDB_PATH) -> None:
    if not forecasts:
        return
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.executemany(
            """
            insert or replace into ml.forecasts
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f.forecast_id, f.forecast_type, f.entity_type, f.entity_id, f.target_date,
                    f.forecast_value, f.lower_bound, f.upper_bound, f.model_name, f.model_version,
                    f.generated_at, f.horizon_days, json.dumps(f.metadata, default=str, sort_keys=True),
                )
                for f in forecasts
            ],
        )
    now = utc_now()
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=f"ml_forecasts_{uuid.uuid4().hex[:8]}",
                source_node="marts.metrics_retailer_daily,marts.fact_orders,marts.fact_product_events",
                target_node="ml.forecasts",
                edge_type="ml_forecast_generated",
                entity="forecasting",
                created_at=now,
            )
        ],
        db_path,
    )


if __name__ == "__main__":
    active_model_version = None
    try:
        from ml.registry import get_active_model

        active = get_active_model(FORECASTING_MODEL_NAME)
        active_model_version = active.version if active else None
    except Exception:
        pass
    results = run_all_forecasts(model_version=active_model_version)
    persist_forecasts(results)
    print(f"Generated {len(results)} forecast rows")
