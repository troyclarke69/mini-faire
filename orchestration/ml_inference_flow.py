"""ML Inference Orchestration (PHASE6-ML.md Section 7, inference half).

Run directly: `python orchestration/ml_inference_flow.py`.

Where orchestration/ml_training_flow.py builds features, trains, evaluates,
and (re)registers each model type, this module is the lighter-weight
companion PHASE6-ML.md Section 7 asks for: "load active models, run
inference, write predictions to warehouse, emit lineage edges." It does not
touch ml/registry.py's write path at all (no register/activate/rollback) -
it only *reads* which version is currently active per model_name via
`registry.get_active_model()`, so a version promoted by the last training
run gets used to tag every prediction until the next training run promotes
a different one.

Intended to run more often than training (e.g. every real-time refresh
cycle or on its own short schedule) so forecasts/clusters/recommendations/
classifications stay current between the comparatively expensive full
retrains - the same "cheap frequent inference, expensive occasional
training" split any real ML system makes. Nothing here currently wires this
into orchestration/realtime_flow.py's cycle automatically (that flow is
Phase 4/5 territory this phase deliberately leaves alone); this module is
meant to be run standalone or from a scheduler, same as
ml_training_flow.py.

Each of the four model types is isolated in its own try/except -
`orchestration/realtime_flow.py`'s `_run_monitoring_pass()` and
`ml_training_flow.py`'s per-model-type loop both establish this repo's "one
bad stage shouldn't take down the others" convention, and it matters even
more here since this is expected to run frequently: a forecasting bug
should never block clustering/recommendations/classification from refreshing.
A stage failure dispatches an `ml_inference_failure` alert (mirroring
`ml_training_flow.py`'s `ml_training_failure`) and that model type's
predictions are simply left stale until the next successful pass, rather
than the whole run aborting.

Forecasting, clustering, and recommendations each refit directly from
current warehouse state on every call (see those modules' docstrings on
"refit fresh vs. persist artifact") - this module's job for those three is
just "look up which version is active, run its refit, persist, done." The
anomaly classifier is the one model type with a genuinely persisted fitted
object; `ml/models/anomaly_classifier.py`'s `run_classification()` already
encapsulates its own active-model lookup and artifact load (via
`ml/registry.py`'s `load_artifact()`), so this module just calls it and
persists the result - it does not unpickle anything itself.

If a model type has no active version yet (training has never run, or every
trained version so far failed its post-activation sanity check and got
rolled back with no earlier good version to land on), that stage is skipped
with a log line rather than treated as a failure - there is nothing to run
inference *with* yet, which is a normal, expected state for a fresh
warehouse before the first `python orchestration/ml_training_flow.py` pass,
not an error.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import utc_now
from ingestion.paths import DUCKDB_PATH
from ml import registry
from ml.config import MLConfig, load_ml_config


def _dispatch_inference_failure(model_type: str, exc: Exception) -> None:
    print(f"  ml_inference_flow: {model_type} inference failed: {exc!r}")
    try:
        from alerts.dispatcher import dispatch_alert

        dispatch_alert(
            "ml_inference_failure",
            entity=f"ml_inference://{model_type}",
            message=f"Inference failed for model type '{model_type}': {exc!r}",
            severity="warning",
        )
    except Exception as dispatch_exc:  # noqa: BLE001 - alerting must never break inference
        print(f"  could not dispatch ml_inference_failure alert: {dispatch_exc!r}")


def _append_inference_run(db_path: Path, *, model_type: str, row_count: int, started_at: str, completed_at: str, status: str) -> None:
    """Persists this stage's wall-clock duration to elt_model_runs
    (load_strategy='ml_inference') - the same table
    ml_training_flow.py's own `_append_elt_run()` writes to
    (load_strategy='ml_training'), just for the inference half. Phase 7
    (PHASE7-DEPLOYMENT.md Section 8, "ML inference duration") adds this:
    run_inference() below already computed `time.monotonic()` timing per
    stage before this change, it just printed it and threw it away rather
    than persisting it anywhere queryable - observability/metrics.py's
    ml_inference_duration_seconds histogram reads this table. Purely
    additive: no existing stage function's logic changes, only a new insert
    using timing that was already being computed."""
    with connect_with_retry(db_path) as con:
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
            "insert into elt_model_runs values (?, ?, 'ml_inference', 'n/a', ?, ?, ?, null, ?, ?, ?)",
            [f"ml_{model_type}", f"ml.{model_type}", row_count, row_count, row_count, started_at, completed_at, status],
        )


def _run_forecasting_inference(db_path: Path, config: MLConfig) -> int:
    from ml.models.forecasting import FORECASTING_MODEL_NAME, persist_forecasts, run_all_forecasts

    active = registry.get_active_model(FORECASTING_MODEL_NAME, db_path)
    if active is None:
        print("  forecasting: no active model registered yet (run orchestration/ml_training_flow.py first) - skipping")
        return 0
    forecasts = run_all_forecasts(db_path, config=config, model_version=active.version)
    persist_forecasts(forecasts, db_path)
    return len(forecasts)


def _run_clustering_inference(db_path: Path, config: MLConfig) -> int:
    from ml.models.clustering import CLUSTERING_MODEL_NAME, persist_clusters, run_all_clustering

    active = registry.get_active_model(CLUSTERING_MODEL_NAME, db_path)
    if active is None:
        print("  clustering: no active model registered yet (run orchestration/ml_training_flow.py first) - skipping")
        return 0
    clusters = run_all_clustering(db_path, config=config, model_version=active.version)
    persist_clusters(clusters, db_path)
    return len(clusters)


def _run_recommendations_inference(db_path: Path, config: MLConfig) -> int:
    from ml.models.recommendations import RECOMMENDATION_MODEL_NAME, persist_recommendations, run_all_recommendations

    active = registry.get_active_model(RECOMMENDATION_MODEL_NAME, db_path)
    if active is None:
        print("  recommendations: no active model registered yet (run orchestration/ml_training_flow.py first) - skipping")
        return 0
    recs = run_all_recommendations(db_path, config=config, model_version=active.version)
    persist_recommendations(recs, db_path)
    return len(recs)


def _run_anomaly_classification_inference(db_path: Path, config: MLConfig) -> int:  # noqa: ARG001 - config kept for signature symmetry with the other three stages
    from ml.models.anomaly_classifier import persist_classifications, run_classification

    # run_classification() does its own get_active_model()/load_artifact()
    # lookup and prints its own "no active model yet" / "artifact missing"
    # messages when there's nothing to classify with - no need to duplicate
    # that check here.
    classifications = run_classification(db_path)
    persist_classifications(classifications, db_path)
    return len(classifications)


STAGES: tuple[tuple[str, Callable[[Path, MLConfig], int]], ...] = (
    ("forecasting", _run_forecasting_inference),
    ("clustering", _run_clustering_inference),
    ("recommendations", _run_recommendations_inference),
    ("anomaly_classifier", _run_anomaly_classification_inference),
)


def run_inference(db_path: Path = DUCKDB_PATH) -> dict[str, int]:
    """Runs all four inference stages, each isolated so one failing stage
    doesn't block the others. Returns {model_type: row_count} - a model type
    that was skipped (no active model yet) or that failed reports 0."""
    if not db_path.exists():
        print("  ml_inference_flow: warehouse not built yet - run scripts/run_demo.py first")
        return {}
    config = load_ml_config()
    if not config.enabled:
        print("  ml_inference_flow: disabled in config/ml.yaml")
        return {}

    results: dict[str, int] = {}
    for model_type, stage in STAGES:
        started_at = utc_now()
        t0 = time.monotonic()
        try:
            results[model_type] = stage(db_path, config)
            print(f"  {model_type} inference pass complete: {results[model_type]} row(s) ({time.monotonic() - t0:.1f}s)")
            status = "success"
        except Exception as exc:  # noqa: BLE001 - one model type failing shouldn't block the rest
            _dispatch_inference_failure(model_type, exc)
            results[model_type] = 0
            status = "failed"

        # Recording the duration is a separate, best-effort step from the
        # stage itself - deliberately outside the try/except above so a
        # transient failure here (e.g. a DuckDB write-lock collision) can
        # never overwrite a successful stage's real row count with 0 or
        # trigger a false ml_inference_failure alert for a stage that
        # actually succeeded.
        try:
            _append_inference_run(
                db_path, model_type=model_type, row_count=results[model_type],
                started_at=started_at, completed_at=utc_now(), status=status,
            )
        except Exception as append_exc:  # noqa: BLE001 - recording the duration must never itself crash the run
            print(f"  ml_inference_flow: could not record {model_type} inference duration: {append_exc!r}")
    return results


if __name__ == "__main__":
    summary = run_inference()
    print(f"ML inference flow complete: {summary}")
