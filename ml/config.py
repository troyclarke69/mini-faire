"""Shared config/ml.yaml loader (PHASE6-ML.md Section 1).

Every ml/models/*.py module and both orchestration/ml_training_flow.py and
ml_inference_flow.py call load_ml_config() rather than reading the YAML file
themselves - one place to change the default path or add a new section
without touching every caller, matching alerts/dispatcher.py's
load_alerts_config() convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ingestion.paths import PROJECT_ROOT

ML_CONFIG_PATH = PROJECT_ROOT / "config" / "ml.yaml"


@dataclass(frozen=True)
class MLConfig:
    enabled: bool
    lookback_days: int
    forecast_horizon_days: int
    forecast_n_lags: int
    forecast_top_n_entities: int
    stockout_inventory_threshold: float
    reorder_inventory_percentile: float
    clustering_method: str
    retailer_k: int
    product_k: int
    dbscan_eps: float
    dbscan_min_samples: int
    gmm_components: int
    recommendation_method: str
    nmf_components: int
    recommendation_top_n: int
    co_occurrence_window_days: int
    trending_window_days: int
    anomaly_classifier_method: str
    anomaly_classifier_min_samples: int
    anomaly_classifier_test_size: float
    min_relative_improvement: float
    raw: dict = field(default_factory=dict)


def load_ml_config(path: Path = ML_CONFIG_PATH) -> MLConfig:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    features = raw.get("feature_engineering") or {}
    forecasting = raw.get("forecasting") or {}
    clustering = raw.get("clustering") or {}
    recommendations = raw.get("recommendations") or {}
    classifier = raw.get("anomaly_classifier") or {}
    promotion = raw.get("model_promotion") or {}

    return MLConfig(
        enabled=bool(raw.get("enabled", True)),
        lookback_days=int(features.get("lookback_days", 30)),
        forecast_horizon_days=int(forecasting.get("horizon_days", 7)),
        forecast_n_lags=int(forecasting.get("n_lags", 7)),
        forecast_top_n_entities=int(forecasting.get("top_n_entities", 10)),
        stockout_inventory_threshold=float(forecasting.get("stockout_inventory_threshold", 0)),
        reorder_inventory_percentile=float(forecasting.get("reorder_inventory_percentile", 15.0)),
        clustering_method=clustering.get("method", "kmeans"),
        retailer_k=int(clustering.get("retailer_k", 4)),
        product_k=int(clustering.get("product_k", 4)),
        dbscan_eps=float(clustering.get("dbscan_eps", 1.5)),
        dbscan_min_samples=int(clustering.get("dbscan_min_samples", 3)),
        gmm_components=int(clustering.get("gmm_components", 4)),
        recommendation_method=recommendations.get("method", "cosine"),
        nmf_components=int(recommendations.get("nmf_components", 8)),
        recommendation_top_n=int(recommendations.get("top_n", 5)),
        co_occurrence_window_days=int(recommendations.get("co_occurrence_window_days", 1)),
        trending_window_days=int(recommendations.get("trending_window_days", 7)),
        anomaly_classifier_method=classifier.get("method", "random_forest"),
        anomaly_classifier_min_samples=int(classifier.get("min_training_samples", 20)),
        anomaly_classifier_test_size=float(classifier.get("test_size", 0.25)),
        min_relative_improvement=float(promotion.get("min_relative_improvement", 0.0)),
        raw=raw,
    )
