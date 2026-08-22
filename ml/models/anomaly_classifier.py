"""Anomaly Classification Model (PHASE6-ML.md Section 6).

Upgrades Phase 5's rule-based anomaly detection (anomalies/detector.py -
rolling mean+std, EWMA, percentile thresholds, z-scores) with an ML
classifier trained on the same table those detectors already populate,
`anomalies.anomaly_events`. Every row already carries a rule-derived
`anomaly_type` label (gmv_spike, gmv_drop, order_velocity_change,
inventory_stockout, price_anomaly, event_lag_spike,
retailer_health_degradation, ingestion_volume_anomaly,
quarantine_rate_anomaly - the full set anomalies/detector.py's eight
detectors actually produce, a superset of PHASE6-ML.md's seven illustrative
category names), so this is naturally a self-supervised setup: the
classifier learns to predict the same `anomaly_type` a rule-based detector
already assigned, from the anomaly's own numeric shape (severity, metric
value, baseline, deviation, which statistical method flagged it, what kind
of entity it's on) - not from ground truth a human labeled.

That framing matters for what "classification confidence" means here: it is
NOT a measure of "is this really an anomaly" (Phase 5's detectors already
decided that), it's "how distinguishable is this anomaly_type from the
others given only its numeric signature" - useful as a secondary signal
(e.g. a low-confidence classification suggests an anomaly whose numeric
profile overlaps several types, worth a closer look) layered on top of,
not replacing, the rule-based detection PHASE6-ML.md explicitly calls "the
later phase" ML upgrade for.

Every anomaly in `anomalies.anomaly_events` is re-classified on each
inference pass (not just newly-detected ones) - simpler than incremental
tracking, and correct: if a new classifier version activates, historical
anomalies should reflect its opinion too, not just future ones. Classifier
choice is config-selectable (`config/ml.yaml`'s `anomaly_classifier.method`):
`random_forest` (default, RandomForestClassifier) or `gradient_boosting`
(GradientBoostingClassifier), both scikit-learn; XGBoost is used instead of
`gradient_boosting` when `xgboost` is importable and NOT explicitly
requested as gradient_boosting - see `_fit()`.

Training is skipped (see `evaluate_anomaly_classifier()`) below
`config/ml.yaml`'s `anomaly_classifier.min_training_samples` or with fewer
than 2 distinct anomaly_types present - fitting sklearn on a handful of
one-class rows would silently produce a model that always predicts the same
label with false confidence, which is worse than not training at all.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DUCKDB_PATH
from ml.config import MLConfig, load_ml_config

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        'ml/models/anomaly_classifier.py requires numpy. Install with `pip install -e ".[ml]"`.'
    ) from exc

try:
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import LabelEncoder
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        'ml/models/anomaly_classifier.py requires scikit-learn. Install with `pip install -e ".[ml]"`.'
    ) from exc

try:
    from xgboost import XGBClassifier
except ImportError:  # optional enhancement - see pyproject.toml's [ml-extra]
    XGBClassifier = None

ANOMALY_CLASSIFIER_MODEL_NAME = "anomaly_classifier"

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
METHOD_CATEGORIES = ["zscore", "percentile_threshold", "ewma"]
ENTITY_TYPE_CATEGORIES = ["system", "product", "retailer", "event_type", "entity"]
FEATURE_NAMES = (
    ["severity_ordinal", "metric_value", "baseline_value", "deviation", "metric_minus_baseline"]
    + [f"method_{m}" for m in METHOD_CATEGORIES]
    + [f"entity_type_{e}" for e in ENTITY_TYPE_CATEGORIES]
)


# ---------------------------------------------------------------------------
# Featurization
# ---------------------------------------------------------------------------


def _featurize_row(severity: str, metric_value: float | None, baseline_value: float | None,
                    deviation: float | None, method: str | None, entity_type: str | None) -> list[float]:
    metric_value = float(metric_value) if metric_value is not None else 0.0
    baseline_value = float(baseline_value) if baseline_value is not None else metric_value
    deviation = float(deviation) if deviation is not None else 0.0
    method_onehot = [1.0 if method == m else 0.0 for m in METHOD_CATEGORIES]
    entity_onehot = [1.0 if entity_type == e else 0.0 for e in ENTITY_TYPE_CATEGORIES]
    return [
        float(SEVERITY_ORDER.get(severity, 0)),
        metric_value, baseline_value, deviation, metric_value - baseline_value,
        *method_onehot, *entity_onehot,
    ]


def _load_anomalies(con) -> list[tuple]:
    return con.execute(
        "select anomaly_id, anomaly_type, severity, metric_value, baseline_value, deviation, method, entity_type "
        "from anomalies.anomaly_events"
    ).fetchall()


def _featurize(rows: list[tuple]) -> tuple[np.ndarray, list[str], list[str]]:
    """rows: (anomaly_id, anomaly_type, severity, metric_value, baseline_value,
    deviation, method, entity_type). Returns (X, y, anomaly_ids)."""
    X, y, ids = [], [], []
    for anomaly_id, anomaly_type, severity, metric_value, baseline_value, deviation, method, entity_type in rows:
        X.append(_featurize_row(severity, metric_value, baseline_value, deviation, method, entity_type))
        y.append(anomaly_type)
        ids.append(anomaly_id)
    return np.array(X, dtype=float), y, ids


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------


def _fit(X: np.ndarray, y_encoded: np.ndarray, *, method: str, seed: int = 42):
    if method == "gradient_boosting":
        model = GradientBoostingClassifier(random_state=seed)
        model.fit(X, y_encoded)
        return model, "gradient_boosting"
    if XGBClassifier is not None:
        # `use_label_encoder` was removed in xgboost>=2.0 and deprecated
        # before that - omitted here so this works across xgboost versions
        # rather than pinning to one deprecation era.
        model = XGBClassifier(n_estimators=200, max_depth=4, random_state=seed, eval_metric="mlogloss")
        model.fit(X, y_encoded)
        return model, "xgboost"
    model = RandomForestClassifier(n_estimators=200, max_depth=8, random_state=seed)
    model.fit(X, y_encoded)
    return model, "random_forest"


def train_classifier(db_path: Path = DUCKDB_PATH, *, config: MLConfig | None = None) -> dict[str, Any] | None:
    """Fits the final classifier on every available labeled anomaly (not a
    train/test split - that's evaluate_anomaly_classifier()'s job). Returns
    a picklable dict bundling the estimator with its label encoding (needed
    to decode predict_proba's columns back to anomaly_type strings) and
    the exact feature-encoding scheme used, or None if there isn't enough
    labeled data to train on (see module docstring)."""
    config = config or load_ml_config()
    if not db_path.exists():
        return None
    with connect_with_retry(db_path, read_only=True) as con:
        rows = _load_anomalies(con)

    X, y, _ids = _featurize(rows)
    if len(y) < config.anomaly_classifier_min_samples or len(set(y)) < 2:
        return None

    encoder = LabelEncoder()
    y_encoded = encoder.fit_transform(y)
    model, method = _fit(X, y_encoded, method=config.anomaly_classifier_method)
    return {"estimator": model, "label_classes": encoder.classes_.tolist(), "method": method, "feature_names": FEATURE_NAMES}


def evaluate_anomaly_classifier(db_path: Path = DUCKDB_PATH, *, config: MLConfig | None = None) -> dict[str, Any]:
    """Held-out accuracy/macro-F1 on a train/test split (higher is better -
    see orchestration/ml_training_flow.py's promotion-gating direction
    table). Returns None-valued metrics (not zero) if there's too little
    data or too few classes to evaluate meaningfully."""
    config = config or load_ml_config()
    if not db_path.exists():
        return {"accuracy": None, "f1_macro": None, "n_samples": 0, "n_classes": 0}
    with connect_with_retry(db_path, read_only=True) as con:
        rows = _load_anomalies(con)

    X, y, _ids = _featurize(rows)
    n_classes = len(set(y))
    if len(y) < config.anomaly_classifier_min_samples or n_classes < 2:
        return {"accuracy": None, "f1_macro": None, "n_samples": len(y), "n_classes": n_classes}

    encoder = LabelEncoder()
    y_encoded = encoder.fit_transform(y)
    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y_encoded, test_size=config.anomaly_classifier_test_size, random_state=42, stratify=y_encoded,
        )
    except ValueError:
        # A class with only 1 member can't be stratified - fall back to a
        # plain random split rather than failing evaluation outright.
        X_train, X_test, y_train, y_test = train_test_split(
            X, y_encoded, test_size=config.anomaly_classifier_test_size, random_state=42,
        )
    if len(X_test) == 0 or len(X_train) == 0:
        return {"accuracy": None, "f1_macro": None, "n_samples": len(y), "n_classes": n_classes}

    model, _method = _fit(X_train, y_train, method=config.anomaly_classifier_method)
    predictions = model.predict(X_test)
    return {
        "accuracy": float(accuracy_score(y_test, predictions)),
        "f1_macro": float(f1_score(y_test, predictions, average="macro", zero_division=0)),
        "n_samples": len(y),
        "n_classes": n_classes,
    }


# ---------------------------------------------------------------------------
# Classification record + inference
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnomalyClassification:
    classification_id: str
    anomaly_id: str
    predicted_type: str
    confidence: float
    actual_type: str
    agrees_with_detector: bool
    model_name: str
    model_version: int | None
    classified_at: str
    metadata: dict[str, Any]


def classify_anomalies(con, bundle: dict[str, Any], *, model_version: int | None = None) -> list[AnomalyClassification]:
    """`bundle` is train_classifier()'s return value (or an equivalent dict
    loaded from ml/registry.py's load_artifact()). Re-classifies every row
    in anomalies.anomaly_events - see module docstring for why this isn't
    incremental."""
    rows = _load_anomalies(con)
    if not rows:
        return []
    X, actual_types, anomaly_ids = _featurize(rows)

    estimator = bundle["estimator"]
    label_classes: list[str] = bundle["label_classes"]
    probabilities = estimator.predict_proba(X)

    classified_at = utc_now()
    results: list[AnomalyClassification] = []
    for i, anomaly_id in enumerate(anomaly_ids):
        proba_row = probabilities[i]
        best_idx = int(np.argmax(proba_row))
        predicted_type = label_classes[best_idx]
        confidence = float(proba_row[best_idx])
        actual_type = actual_types[i]
        results.append(
            AnomalyClassification(
                classification_id=f"classification_{anomaly_id}",
                anomaly_id=anomaly_id,
                predicted_type=predicted_type,
                confidence=confidence,
                actual_type=actual_type,
                agrees_with_detector=(predicted_type == actual_type),
                model_name=ANOMALY_CLASSIFIER_MODEL_NAME,
                model_version=model_version,
                classified_at=classified_at,
                metadata={"probabilities": dict(zip(label_classes, (float(p) for p in proba_row)))},
            )
        )
    return results


def run_classification(db_path: Path = DUCKDB_PATH, *, model_version: int | None = None) -> list[AnomalyClassification]:
    if not db_path.exists():
        return []
    from ml.registry import get_active_model, load_artifact

    active = get_active_model(ANOMALY_CLASSIFIER_MODEL_NAME, db_path)
    if active is None:
        print("  no active anomaly_classifier model registered yet - skipping classification")
        return []
    bundle = load_artifact(active.artifact_path)
    if bundle is None:
        print(f"  anomaly_classifier model artifact missing ({active.artifact_path!r}) - skipping classification")
        return []

    with connect_with_retry(db_path, read_only=True) as con:
        return classify_anomalies(con, bundle, model_version=model_version if model_version is not None else active.version)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists ml")
    con.execute(
        """
        create table if not exists ml.anomaly_classifications (
          classification_id varchar primary key,
          anomaly_id varchar,
          predicted_type varchar,
          confidence double,
          actual_type varchar,
          agrees_with_detector boolean,
          model_name varchar,
          model_version integer,
          classified_at timestamptz,
          metadata varchar
        )
        """
    )


def persist_classifications(classifications: list[AnomalyClassification], db_path: Path = DUCKDB_PATH) -> None:
    if not classifications:
        return
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.executemany(
            """
            insert or replace into ml.anomaly_classifications
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    c.classification_id, c.anomaly_id, c.predicted_type, c.confidence, c.actual_type,
                    c.agrees_with_detector, c.model_name, c.model_version, c.classified_at,
                    json.dumps(c.metadata, default=str, sort_keys=True),
                )
                for c in classifications
            ],
        )
    now = utc_now()
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=f"ml_anomaly_classifications_{uuid.uuid4().hex[:8]}",
                source_node="anomalies.anomaly_events",
                target_node="ml.anomaly_classifications",
                edge_type="ml_anomaly_classified",
                entity="anomaly_classifier",
                created_at=now,
            )
        ],
        db_path,
    )


if __name__ == "__main__":
    results = run_classification()
    persist_classifications(results)
    print(f"Classified {len(results)} anomalies")
