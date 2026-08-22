"""ML Training Orchestration (PHASE6-ML.md Section 7, training half).

Run directly: `python orchestration/ml_training_flow.py`.

Four model types (forecasting, clustering, recommendations, anomaly
classifier - PHASE6-ML.md Sections 3-6), each isolated in its own
try/except so one broken model type never blocks the others - the same "one
bad stage shouldn't stop the pipeline" philosophy orchestration/
realtime_flow.py's Section 7 integration and monitoring/metrics.py's
`_safe()` already use. Sequence per model type:

1. Build features (`ml/features/build_features.py`, once for the whole run
   - every model type reads from the same `ml.features` snapshot).
2. Evaluate (each `ml/models/*.py`'s `evaluate_*()` - a backtest MAE for
   forecasting, silhouette score for clustering, held-out accuracy/F1 for
   the anomaly classifier, or a coverage metric for recommendations, which
   has no ground truth to score against - see that module's docstring).
3. Register the new version in `ml/registry.py` as `inactive`.
4. Promotion gate (`_is_improvement()`): activate only if there's no prior
   active version, or the new version's eval metric beats the active
   version's by at least `config/ml.yaml`'s `model_promotion.
   min_relative_improvement` - direction (higher/lower is better) depends
   on the model type, see PROMOTION_DIRECTIONS. Recommendations have no
   scalar quality metric in this synthetic dataset, so they're always
   promoted (documented in that module, not faked here).
5. Post-activation sanity check: run the newly-activated version for real
   (`run_all_forecasts()` / `run_all_clustering()` / `run_all_recommendations()`
   / a classification pass) and persist the result. If that raises,
   `ml/registry.py`'s `rollback_model()` reverts to the previous active
   version and an `ml_training_failure` alert is dispatched -
   PHASE6-ML.md's "rollback on failure" given real teeth: a version that
   promotes cleanly but then can't actually produce output never stays
   active.
6. One `elt_model_runs` row appended per model type (PHASE6-ML.md: "append
   training runs to elt_model_runs"), `load_strategy = 'ml_training'`, so
   the frontend's ELT Model Runs view shows ML training activity alongside
   SQL models and Polars compute - same convention Phase 5's
   `compute/polars/compute_metrics.py` established for Polars runs.

Forecasting, clustering, and recommendations have no single persisted
estimator (`ml/registry.py`'s `register_model(estimator=None, ...)`) - each
refits directly from current warehouse state whenever `run_all_*()` is
called, matching this repo's existing "recompute fully rather than
incrementally" philosophy for the compute layer (`compute/polars/
compute_metrics.py`'s `create or replace table` pattern). Only the anomaly
classifier persists a real fitted object (a classifier + label-encoding
bundle, pickled via `ml/registry.py`'s `save_artifact()`), since refitting
it from `anomalies.anomaly_events` on every inference pass would be both
wasteful and would make "which model version classified this anomaly"
meaningless.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import utc_now
from ingestion.paths import DUCKDB_PATH
from ml import registry
from ml.config import load_ml_config
from ml.features.build_features import build_all_features

# model_name -> (metric_key to compare, direction). "always" means every
# newly-trained version is promoted unconditionally (recommendations - see
# that module's docstring on why there's no ground-truth quality score).
PROMOTION_DIRECTIONS: dict[str, tuple[str | None, str]] = {
    "forecasting": ("mae", "lower_better"),
    "clustering": ("silhouette", "higher_better"),
    "anomaly_classifier": ("f1_macro", "higher_better"),
    "recommendations": (None, "always"),
}


def _is_improvement(model_name: str, new_metrics: dict[str, Any], active_metrics: dict[str, Any] | None, *, min_relative_improvement: float) -> bool:
    metric_key, direction = PROMOTION_DIRECTIONS[model_name]
    if direction == "always":
        return True
    if active_metrics is None:
        return True  # first version ever for this model_name
    new_value = new_metrics.get(metric_key)
    if new_value is None:
        return False  # couldn't evaluate the new version - never promote an unevaluated model over a working one
    active_value = active_metrics.get(metric_key)
    if active_value is None:
        return True  # active version was never evaluated (shouldn't normally happen) - anything measurable beats that
    if direction == "lower_better":
        baseline = abs(active_value) or 1.0
        return (active_value - new_value) / baseline >= min_relative_improvement
    baseline = abs(active_value) or 1.0
    return (new_value - active_value) / baseline >= min_relative_improvement


def _append_elt_run(con, *, model_name: str, target_table: str, row_count: int, started_at: str, completed_at: str, status: str) -> None:
    con.execute(
        """
        create table if not exists elt_model_runs (
          model_name varchar, target_table varchar, load_strategy varchar, business_key varchar,
          source_row_count integer, affected_key_count integer, target_row_count integer,
          high_watermark timestamptz, started_at timestamptz, completed_at timestamptz, status varchar
        )
        """
    )
    con.execute(
        "insert into elt_model_runs values (?, ?, 'ml_training', 'n/a', ?, ?, ?, null, ?, ?, ?)",
        [model_name, target_table, row_count, row_count, row_count, started_at, completed_at, status],
    )


def _rollback_and_report(model_name: str, model_type: str, exc: Exception, db_path: Path) -> str:
    """Calls registry.rollback_model() after a post-activation sanity check
    fails, dispatches the failure alert, and returns the elt_model_runs
    status that actually matches what happened: 'rolled_back' only if
    rollback_model() found an earlier known-good version to revert to;
    'sanity_check_failed_no_rollback_target' if this was the first-ever
    version (or every earlier version was registered but never promoted -
    see rollback_model()'s docstring), in which case the just-activated,
    just-failed version is left active for lack of any better option."""
    rolled_back = registry.rollback_model(model_name, db_path)
    _dispatch_training_failure(model_type, exc)
    return "rolled_back" if rolled_back is not None else "sanity_check_failed_no_rollback_target"


def _dispatch_training_failure(model_type: str, exc: Exception) -> None:
    print(f"  ml_training_flow: {model_type} training failed: {exc!r}")
    try:
        from alerts.dispatcher import dispatch_alert

        dispatch_alert(
            "ml_training_failure",
            entity=f"ml_training://{model_type}",
            message=f"Training failed for model type '{model_type}': {exc!r}",
            severity="critical",
        )
    except Exception as dispatch_exc:  # noqa: BLE001 - alerting must never break training
        print(f"  could not dispatch ml_training_failure alert: {dispatch_exc!r}")


def _train_forecasting(db_path: Path, config, target_table="ml.forecasts") -> None:
    from ml.models.forecasting import FORECASTING_MODEL_NAME, evaluate_forecasting, persist_forecasts, run_all_forecasts

    started_at = utc_now()
    metrics = evaluate_forecasting(db_path, config=config)
    active = registry.get_active_model(FORECASTING_MODEL_NAME, db_path)
    record = registry.register_model(
        FORECASTING_MODEL_NAME, "forecasting",
        params={"horizon_days": config.forecast_horizon_days, "n_lags": config.forecast_n_lags,
                "top_n_entities": config.forecast_top_n_entities},
        metrics=metrics, feature_schema=["gmv_daily", "gmv_weekly", "gmv_retailer",
                                          "velocity_product", "velocity_retailer", "inventory_level", "price_trend"],
        estimator=None, activate=False, db_path=db_path,
    )
    promote = _is_improvement("forecasting", metrics, active.metrics if active else None,
                               min_relative_improvement=config.min_relative_improvement)
    if not promote:
        print(f"  forecasting v{record.version} not promoted (metrics {metrics} vs active {active.metrics if active else None})")
        with connect_with_retry(db_path) as con:
            _append_elt_run(con, model_name="ml_forecasting", target_table=target_table, row_count=0,
                             started_at=started_at, completed_at=utc_now(), status="not_promoted")
        return

    registry.activate_model(FORECASTING_MODEL_NAME, record.version, db_path)
    try:
        forecasts = run_all_forecasts(db_path, config=config, model_version=record.version)
        persist_forecasts(forecasts, db_path)
        status, row_count = "success", len(forecasts)
    except Exception as exc:  # noqa: BLE001 - post-activation sanity check failed
        status = _rollback_and_report(FORECASTING_MODEL_NAME, "forecasting", exc, db_path)
        row_count = 0

    with connect_with_retry(db_path) as con:
        _append_elt_run(con, model_name="ml_forecasting", target_table=target_table, row_count=row_count,
                         started_at=started_at, completed_at=utc_now(), status=status)


def _train_clustering(db_path: Path, config, target_table="ml.clusters") -> None:
    from ml.models.clustering import CLUSTERING_MODEL_NAME, evaluate_clustering, persist_clusters, run_all_clustering

    started_at = utc_now()
    metrics = evaluate_clustering(db_path, config=config)
    active = registry.get_active_model(CLUSTERING_MODEL_NAME, db_path)
    record = registry.register_model(
        CLUSTERING_MODEL_NAME, "clustering",
        params={"method": config.clustering_method, "retailer_k": config.retailer_k, "product_k": config.product_k},
        metrics=metrics, feature_schema=["retailer_features", "product_features"],
        estimator=None, activate=False, db_path=db_path,
    )
    promote = _is_improvement("clustering", metrics, active.metrics if active else None,
                               min_relative_improvement=config.min_relative_improvement)
    if not promote:
        print(f"  clustering v{record.version} not promoted (metrics {metrics} vs active {active.metrics if active else None})")
        with connect_with_retry(db_path) as con:
            _append_elt_run(con, model_name="ml_clustering", target_table=target_table, row_count=0,
                             started_at=started_at, completed_at=utc_now(), status="not_promoted")
        return

    registry.activate_model(CLUSTERING_MODEL_NAME, record.version, db_path)
    try:
        clusters = run_all_clustering(db_path, config=config, model_version=record.version)
        persist_clusters(clusters, db_path)
        status, row_count = "success", len(clusters)
    except Exception as exc:  # noqa: BLE001
        status = _rollback_and_report(CLUSTERING_MODEL_NAME, "clustering", exc, db_path)
        row_count = 0

    with connect_with_retry(db_path) as con:
        _append_elt_run(con, model_name="ml_clustering", target_table=target_table, row_count=row_count,
                         started_at=started_at, completed_at=utc_now(), status=status)


def _train_recommendations(db_path: Path, config, target_table="ml.recommendations") -> None:
    from ml.models.recommendations import RECOMMENDATION_MODEL_NAME, evaluate_recommendations, persist_recommendations, run_all_recommendations

    started_at = utc_now()
    metrics = evaluate_recommendations(db_path, config=config)
    record = registry.register_model(
        RECOMMENDATION_MODEL_NAME, "recommendations",
        params={"method": config.recommendation_method, "top_n": config.recommendation_top_n},
        metrics=metrics, feature_schema=["retailer_product_interaction_matrix"],
        estimator=None, activate=False, db_path=db_path,
    )
    # Always promoted - see module docstring on why there's no ground-truth
    # quality score to gate on for recommendations in this dataset.
    registry.activate_model(RECOMMENDATION_MODEL_NAME, record.version, db_path)
    try:
        recs = run_all_recommendations(db_path, config=config, model_version=record.version)
        persist_recommendations(recs, db_path)
        status, row_count = "success", len(recs)
    except Exception as exc:  # noqa: BLE001
        status = _rollback_and_report(RECOMMENDATION_MODEL_NAME, "recommendations", exc, db_path)
        row_count = 0

    with connect_with_retry(db_path) as con:
        _append_elt_run(con, model_name="ml_recommendations", target_table=target_table, row_count=row_count,
                         started_at=started_at, completed_at=utc_now(), status=status)


def _train_anomaly_classifier(db_path: Path, config, target_table="ml.anomaly_classifications") -> None:
    from ml.models.anomaly_classifier import (
        ANOMALY_CLASSIFIER_MODEL_NAME, classify_anomalies, evaluate_anomaly_classifier,
        persist_classifications, train_classifier,
    )

    started_at = utc_now()
    metrics = evaluate_anomaly_classifier(db_path, config=config)
    bundle = train_classifier(db_path, config=config)
    if bundle is None:
        print(f"  anomaly_classifier: not enough labeled anomalies to train yet (metrics={metrics})")
        with connect_with_retry(db_path) as con:
            _append_elt_run(con, model_name="ml_anomaly_classifier", target_table=target_table, row_count=0,
                             started_at=started_at, completed_at=utc_now(), status="skipped_insufficient_data")
        return

    active = registry.get_active_model(ANOMALY_CLASSIFIER_MODEL_NAME, db_path)
    record = registry.register_model(
        ANOMALY_CLASSIFIER_MODEL_NAME, "anomaly_classifier",
        params={"method": bundle["method"]}, metrics=metrics, feature_schema=bundle["feature_names"],
        estimator=bundle, activate=False, db_path=db_path,
    )
    promote = _is_improvement("anomaly_classifier", metrics, active.metrics if active else None,
                               min_relative_improvement=config.min_relative_improvement)
    if not promote:
        print(f"  anomaly_classifier v{record.version} not promoted (metrics {metrics} vs active {active.metrics if active else None})")
        with connect_with_retry(db_path) as con:
            _append_elt_run(con, model_name="ml_anomaly_classifier", target_table=target_table, row_count=0,
                             started_at=started_at, completed_at=utc_now(), status="not_promoted")
        return

    registry.activate_model(ANOMALY_CLASSIFIER_MODEL_NAME, record.version, db_path)
    try:
        with connect_with_retry(db_path, read_only=True) as con:
            classifications = classify_anomalies(con, bundle, model_version=record.version)
        persist_classifications(classifications, db_path)
        status, row_count = "success", len(classifications)
    except Exception as exc:  # noqa: BLE001
        status = _rollback_and_report(ANOMALY_CLASSIFIER_MODEL_NAME, "anomaly_classifier", exc, db_path)
        row_count = 0

    with connect_with_retry(db_path) as con:
        _append_elt_run(con, model_name="ml_anomaly_classifier", target_table=target_table, row_count=row_count,
                         started_at=started_at, completed_at=utc_now(), status=status)


TRAINERS: tuple[tuple[str, Callable], ...] = (
    ("forecasting", _train_forecasting),
    ("clustering", _train_clustering),
    ("recommendations", _train_recommendations),
    ("anomaly_classifier", _train_anomaly_classifier),
)


def run_training(db_path: Path = DUCKDB_PATH) -> None:
    if not db_path.exists():
        print("  ml_training_flow: warehouse not built yet - run scripts/run_demo.py first")
        return
    config = load_ml_config()
    if not config.enabled:
        print("  ml_training_flow: disabled in config/ml.yaml")
        return

    print(f"  [{utc_now()}] building features...")
    features = build_all_features(db_path)
    print(f"  built {len(features)} feature rows")

    for model_type, trainer in TRAINERS:
        t0 = time.monotonic()
        try:
            trainer(db_path, config)
        except Exception as exc:  # noqa: BLE001 - one model type failing shouldn't block the rest
            _dispatch_training_failure(model_type, exc)
            continue
        print(f"  {model_type} training pass complete ({time.monotonic() - t0:.1f}s)")


if __name__ == "__main__":
    run_training()
    print("ML training flow complete")
