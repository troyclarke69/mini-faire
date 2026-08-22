"""Clustering Models (PHASE6-ML.md Section 4).

One registry model_name, `"clustering"` (CLUSTERING_MODEL_NAME), covers both
retailer and product segmentation - same reasoning as
ml/models/forecasting.py's single "forecasting" entry: one evaluation metric
(mean silhouette score across both entity types, see
`evaluate_clustering()`) governs promotion for the whole clustering
subsystem rather than two independently-versioned registry entries.

Both segmentations read already-built rows from `ml.features` (see
ml/features/build_features.py) rather than re-deriving feature values from
the warehouse - this is the intended consumer relationship PHASE6-ML.md
Section 2 sets up ("unified feature store for ML models"). Product
clustering additionally reads `unit_price`/`unit_cost` directly from
`marts.dim_product` for a margin feature, since build_features.py's product
group doesn't include one.

Method is config-selectable (`config/ml.yaml`'s `clustering.method`):
KMeans (default), DBSCAN, or Gaussian Mixture, all via scikit-learn. Every
method's per-entity output goes through the same post-processing: features
are standardized (`StandardScaler`), reduced to 2 components via PCA for
`plot_x`/`plot_y` (so the frontend's ClusterMap can render a real 2D
scatter instead of an arbitrary layout), and each numeric raw cluster label
is turned into one of PHASE6-ML.md's named segments.

Segment naming (`_label_cluster()`): PHASE6-ML.md's segment names describe
independent traits (a retailer can be simultaneously high-GMV *and*
anomaly-prone), but a partition-based clustering algorithm assigns each
point to exactly one group. This module reconciles the two by choosing, per
cluster, whichever trait axis (velocity, GMV, anomaly rate for retailers;
velocity, margin, inventory volatility for products) that cluster's
centroid deviates from the population mean by the most (largest |z-score|),
and labels the cluster with that axis's corresponding name. This is a
genuine judgment call bridging the spec's descriptive segment list and an
unsupervised algorithm's numeric partition - documented here rather than
silently picked.

DBSCAN's noise points (raw label -1, no meaningful centroid) are always
labeled "outlier" regardless of the trait-axis logic above - trying to
force a "which trait is most extreme" story onto a group DBSCAN explicitly
identified as not belonging to any cluster would be misleading.
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
        'ml/models/clustering.py requires numpy. Install with `pip install -e ".[ml]"`.'
    ) from exc

try:
    from sklearn.cluster import DBSCAN, KMeans
    from sklearn.decomposition import PCA
    from sklearn.metrics import silhouette_score
    from sklearn.mixture import GaussianMixture
    from sklearn.preprocessing import StandardScaler
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        'ml/models/clustering.py requires scikit-learn. Install with `pip install -e ".[ml]"`.'
    ) from exc

CLUSTERING_MODEL_NAME = "clustering"

RETAILER_FEATURE_KEYS = ["daily_gmv", "velocity", "anomaly_count", "retailer_health_score"]
RETAILER_AXES = [
    ("velocity", "high_velocity", "low_velocity"),
    ("daily_gmv", "high_gmv", "low_gmv"),
    ("anomaly_count", "anomaly_prone", None),
]
RETAILER_DEFAULT_SEGMENT = "stable"

PRODUCT_FEATURE_KEYS = ["units_sold", "margin", "inventory_volatility"]
PRODUCT_AXES = [
    ("units_sold", "fast_movers", "slow_movers"),
    ("margin", "high_margin", "low_margin"),
    ("inventory_volatility", "volatile_inventory", "stable_inventory"),
]
PRODUCT_DEFAULT_SEGMENT = "stable"

OUTLIER_SEGMENT = "outlier"


# ---------------------------------------------------------------------------
# Cluster record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cluster:
    cluster_id: str
    entity_type: str
    entity_id: str
    cluster_label: int
    segment_name: str
    plot_x: float
    plot_y: float
    method: str
    model_name: str
    model_version: int | None
    computed_at: str
    metadata: dict[str, Any]


def _cluster_id(entity_type: str, entity_id: str) -> str:
    # Deterministic - one current cluster assignment per entity, replaced on
    # each inference pass rather than accumulated as history (same reasoning
    # as ml/models/forecasting.py's forecast_id).
    safe_entity = "".join(ch if ch.isalnum() else "_" for ch in str(entity_id))[:40]
    return f"cluster_{entity_type}_{safe_entity}"


# ---------------------------------------------------------------------------
# Shared clustering pipeline
# ---------------------------------------------------------------------------


def _fit_labels(X: np.ndarray, *, method: str, k: int, dbscan_eps: float, dbscan_min_samples: int, gmm_components: int, seed: int = 42) -> tuple[np.ndarray, str]:
    n = X.shape[0]
    if method == "dbscan":
        model = DBSCAN(eps=dbscan_eps, min_samples=min(dbscan_min_samples, max(1, n - 1)))
        return model.fit_predict(X), "dbscan"
    if method == "gmm":
        n_components = max(1, min(gmm_components, n))
        model = GaussianMixture(n_components=n_components, random_state=seed)
        return model.fit_predict(X), "gmm"
    # default: kmeans
    n_clusters = max(1, min(k, n))
    model = KMeans(n_clusters=n_clusters, random_state=seed, n_init=10)
    return model.fit_predict(X), "kmeans"


def _label_cluster(centroid_z: dict[str, float], axes: list[tuple[str, str | None, str | None]], default: str) -> str:
    ordered = sorted(axes, key=lambda a: abs(centroid_z.get(a[0], 0.0)), reverse=True)
    for feature_name, pos_label, neg_label in ordered:
        z = centroid_z.get(feature_name, 0.0)
        label = pos_label if z >= 0 else neg_label
        if label is not None:
            return label
    return default


def _cluster_entities(
    entity_type: str,
    entities: list[tuple[str, dict[str, float]]],
    feature_keys: list[str],
    axes: list[tuple[str, str | None, str | None]],
    default_segment: str,
    *,
    config: MLConfig,
    model_version: int | None,
) -> list[Cluster]:
    if len(entities) < 2:
        return []

    entity_ids = [e[0] for e in entities]
    raw_matrix = np.array([[feats.get(key, 0.0) for key in feature_keys] for _entity_id, feats in entities], dtype=float)

    scaler = StandardScaler()
    X = scaler.fit_transform(raw_matrix)

    k = config.retailer_k if entity_type == "retailer" else config.product_k
    labels, method = _fit_labels(
        X, method=config.clustering_method, k=k,
        dbscan_eps=config.dbscan_eps, dbscan_min_samples=config.dbscan_min_samples,
        gmm_components=config.gmm_components,
    )

    n_components = min(2, X.shape[1]) if X.shape[1] >= 1 else 0
    if n_components >= 2 and X.shape[0] >= 2:
        coords = PCA(n_components=2, random_state=42).fit_transform(X)
    else:
        coords = np.zeros((X.shape[0], 2))

    global_mean = X.mean(axis=0)
    global_std = X.std(axis=0)
    global_std[global_std == 0] = 1.0

    computed_at = utc_now()
    rows: list[Cluster] = []
    for unique_label in sorted(set(labels.tolist())):
        mask = labels == unique_label
        centroid = X[mask].mean(axis=0)
        centroid_z = {key: float((centroid[i] - global_mean[i]) / global_std[i]) for i, key in enumerate(feature_keys)}

        if unique_label == -1:
            segment_name = OUTLIER_SEGMENT
        else:
            segment_name = _label_cluster(centroid_z, axes, default_segment)

        for idx in np.where(mask)[0]:
            entity_id = entity_ids[idx]
            raw_features = dict(zip(feature_keys, raw_matrix[idx].tolist()))
            rows.append(
                Cluster(
                    cluster_id=_cluster_id(entity_type, entity_id),
                    entity_type=entity_type,
                    entity_id=str(entity_id),
                    cluster_label=int(unique_label),
                    segment_name=segment_name,
                    plot_x=float(coords[idx][0]),
                    plot_y=float(coords[idx][1]),
                    method=method,
                    model_name=CLUSTERING_MODEL_NAME,
                    model_version=model_version,
                    computed_at=computed_at,
                    metadata={"features": raw_features, "centroid_z": centroid_z},
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Feature loading from ml.features
# ---------------------------------------------------------------------------


def _load_feature_entities(con, feature_group: str) -> list[tuple[str, dict[str, float]]]:
    rows = con.execute(
        "select entity_id, features from ml.features where feature_group = ? order by computed_at desc",
        [feature_group],
    ).fetchall()
    # ml.features accumulates history (unlike ml.clusters, which is
    # replaced) - keep only the most recent row per entity_id.
    seen: set[str] = set()
    out: list[tuple[str, dict[str, float]]] = []
    for entity_id, features_json in rows:
        if entity_id in seen:
            continue
        seen.add(entity_id)
        try:
            out.append((entity_id, json.loads(features_json) if features_json else {}))
        except json.JSONDecodeError:
            continue
    return out


def _load_product_margins(con) -> dict[str, float]:
    rows = con.execute(
        "select product_id, unit_price, unit_cost from marts.dim_product where is_active and unit_price > 0"
    ).fetchall()
    return {
        product_id: float((unit_price - unit_cost) / unit_price)
        for product_id, unit_price, unit_cost in rows
        if unit_price and unit_price > 0
    }


# ---------------------------------------------------------------------------
# Inference entry points
# ---------------------------------------------------------------------------


def cluster_retailers(con, *, config: MLConfig, model_version: int | None = None) -> list[Cluster]:
    entities = _load_feature_entities(con, "retailer")
    return _cluster_entities("retailer", entities, RETAILER_FEATURE_KEYS, RETAILER_AXES, RETAILER_DEFAULT_SEGMENT,
                              config=config, model_version=model_version)


def cluster_products(con, *, config: MLConfig, model_version: int | None = None) -> list[Cluster]:
    entities = _load_feature_entities(con, "product")
    margins = _load_product_margins(con)
    enriched = [(entity_id, {**features, "margin": margins.get(entity_id, 0.0)}) for entity_id, features in entities]
    return _cluster_entities("product", enriched, PRODUCT_FEATURE_KEYS, PRODUCT_AXES, PRODUCT_DEFAULT_SEGMENT,
                              config=config, model_version=model_version)


def run_all_clustering(db_path: Path = DUCKDB_PATH, *, config: MLConfig | None = None, model_version: int | None = None) -> list[Cluster]:
    if not db_path.exists():
        return []
    config = config or load_ml_config()
    clusters: list[Cluster] = []
    with connect_with_retry(db_path, read_only=True) as con:
        for label, builder in (("retailer", cluster_retailers), ("product", cluster_products)):
            try:
                clusters.extend(builder(con, config=config, model_version=model_version))
            except Exception as exc:  # noqa: BLE001 - one entity type failing shouldn't block the other
                print(f"  clustering group '{label}' failed: {exc!r}")
    return clusters


# ---------------------------------------------------------------------------
# Evaluation (for registry promotion gating)
# ---------------------------------------------------------------------------


def evaluate_clustering(db_path: Path = DUCKDB_PATH, *, config: MLConfig | None = None) -> dict[str, Any]:
    """Mean silhouette score across retailer and product clustering (higher
    is better, range roughly [-1, 1]). Either or both may be None if there
    aren't enough entities/distinct clusters to compute a silhouette score
    (needs at least 2 clusters and more samples than clusters) - callers
    must treat a None "silhouette" as "can't evaluate yet", not a score of
    zero."""
    if not db_path.exists():
        return {"silhouette": None, "retailer_silhouette": None, "product_silhouette": None}
    config = config or load_ml_config()

    scores: dict[str, float | None] = {"retailer_silhouette": None, "product_silhouette": None}
    with connect_with_retry(db_path, read_only=True) as con:
        for key, feature_group, feature_keys, extra in (
            ("retailer_silhouette", "retailer", RETAILER_FEATURE_KEYS, None),
            ("product_silhouette", "product", PRODUCT_FEATURE_KEYS, _load_product_margins),
        ):
            entities = _load_feature_entities(con, feature_group)
            if extra is not None:
                margins = extra(con)
                entities = [(eid, {**feats, "margin": margins.get(eid, 0.0)}) for eid, feats in entities]
            if len(entities) < 3:
                continue
            raw_matrix = np.array([[feats.get(k, 0.0) for k in feature_keys] for _eid, feats in entities], dtype=float)
            X = StandardScaler().fit_transform(raw_matrix)
            k = config.retailer_k if feature_group == "retailer" else config.product_k
            labels, _method = _fit_labels(
                X, method=config.clustering_method, k=k,
                dbscan_eps=config.dbscan_eps, dbscan_min_samples=config.dbscan_min_samples,
                gmm_components=config.gmm_components,
            )
            if len(set(labels.tolist())) < 2 or len(set(labels.tolist())) >= len(entities):
                continue
            try:
                scores[key] = float(silhouette_score(X, labels))
            except ValueError:
                continue

    available = [v for v in scores.values() if v is not None]
    return {"silhouette": (sum(available) / len(available)) if available else None, **scores}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists ml")
    con.execute(
        """
        create table if not exists ml.clusters (
          cluster_id varchar primary key,
          entity_type varchar,
          entity_id varchar,
          cluster_label integer,
          segment_name varchar,
          plot_x double,
          plot_y double,
          method varchar,
          model_name varchar,
          model_version integer,
          computed_at timestamptz,
          metadata varchar
        )
        """
    )


def persist_clusters(clusters: list[Cluster], db_path: Path = DUCKDB_PATH) -> None:
    if not clusters:
        return
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.executemany(
            """
            insert or replace into ml.clusters
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    c.cluster_id, c.entity_type, c.entity_id, c.cluster_label, c.segment_name,
                    c.plot_x, c.plot_y, c.method, c.model_name, c.model_version, c.computed_at,
                    json.dumps(c.metadata, default=str, sort_keys=True),
                )
                for c in clusters
            ],
        )
    now = utc_now()
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=f"ml_clusters_{uuid.uuid4().hex[:8]}",
                source_node="ml.features",
                target_node="ml.clusters",
                edge_type="ml_cluster_assigned",
                entity="clustering",
                created_at=now,
            )
        ],
        db_path,
    )


if __name__ == "__main__":
    active_model_version = None
    try:
        from ml.registry import get_active_model

        active = get_active_model(CLUSTERING_MODEL_NAME)
        active_model_version = active.version if active else None
    except Exception:
        pass
    results = run_all_clustering(model_version=active_model_version)
    persist_clusters(results)
    print(f"Assigned {len(results)} cluster rows")
