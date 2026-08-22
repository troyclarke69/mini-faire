"""Recommendation Models (PHASE6-ML.md Section 5).

One registry model_name, `"recommendations"` (RECOMMENDATION_MODEL_NAME),
covers every recommendation type below - same single-registry-entry
reasoning as forecasting/clustering. Unlike those two, there's no held-out
ground truth to score recommendation quality against in this synthetic
dataset (no real user ever clicked "yes, that was a good recommendation"),
so `evaluate_recommendations()` reports a coverage metric (what fraction of
products/retailers got at least one recommendation) rather than a
precision/recall-style score, and orchestration/ml_training_flow.py
activates every newly-trained recommendations version unconditionally -
documented there rather than fabricating a quality number this dataset
can't actually support.

This schema's `marts.fact_orders` grain is one row per (retailer, product,
order) - there is no multi-item shopping basket, so PHASE6-ML.md's
"products frequently bought together" (classically a basket-co-occurrence
signal) is adapted to "products the same retailer ordered within
`co_occurrence_window_days` of each other" - the closest available proxy for
co-purchase behavior in a schema where each order is a single line item.

Everything else is built from one retailer x product interaction matrix
(`_interaction_matrix()`: rows=retailers, columns=products, cell=order
count in the lookback window) via two selectable methods
(`config/ml.yaml`'s `recommendations.method`):

- `cosine` (default): item-item / retailer-retailer cosine similarity
  directly on the raw interaction matrix - classic collaborative filtering.
- `nmf`: non-negative matrix factorization (scikit-learn's `NMF`) reduces
  the interaction matrix to `nmf_components` latent factors per
  retailer/product first, then cosine similarity runs on those factor
  vectors instead of the raw (and much sparser) interaction rows -
  PHASE6-ML.md's explicit "matrix factorization" suggestion.

"products similar to X" / "retailers similar to X" both come from this same
similarity computation, just read from opposite axes of the same matrix.
"trending in category Y" and "retailers likely to grow" are lighter-weight
trend signals (recent vs. prior period comparison / linear slope), not
similarity-based - see `product_trending()` / `retailer_growth()`.
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
        'ml/models/recommendations.py requires numpy. Install with `pip install -e ".[ml]"`.'
    ) from exc

try:
    from sklearn.decomposition import NMF
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        'ml/models/recommendations.py requires scikit-learn. Install with `pip install -e ".[ml]"`.'
    ) from exc

RECOMMENDATION_MODEL_NAME = "recommendations"
INTERACTION_LOOKBACK_DAYS = 60
CO_OCCURRENCE_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str
    recommendation_type: str
    source_entity_type: str
    source_entity_id: str
    recommended_entity_type: str
    recommended_entity_id: str
    score: float
    rank: int
    method: str
    model_name: str
    model_version: int | None
    generated_at: str
    metadata: dict[str, Any]


def _recommendation_id(rec_type: str, source_id: str, recommended_id: str) -> str:
    # Deterministic - one current recommendation edge per (type, source,
    # recommended) pair, replaced each inference pass rather than accumulated.
    def _safe(s: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in str(s))[:30]

    return f"rec_{rec_type}_{_safe(source_id)}_{_safe(recommended_id)}"


def _make_rows(rec_type: str, source_type: str, recommended_type: str, edges: dict[str, list[tuple[str, float]]],
               *, method: str, model_version: int | None, extra_metadata: dict[str, Any] | None = None) -> list[Recommendation]:
    generated_at = utc_now()
    rows: list[Recommendation] = []
    for source_id, recs in edges.items():
        for rank, (recommended_id, score) in enumerate(recs, start=1):
            rows.append(
                Recommendation(
                    recommendation_id=_recommendation_id(rec_type, source_id, recommended_id),
                    recommendation_type=rec_type,
                    source_entity_type=source_type,
                    source_entity_id=str(source_id),
                    recommended_entity_type=recommended_type,
                    recommended_entity_id=str(recommended_id),
                    score=float(score),
                    rank=rank,
                    method=method,
                    model_name=RECOMMENDATION_MODEL_NAME,
                    model_version=model_version,
                    generated_at=generated_at,
                    metadata=extra_metadata or {},
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Interaction matrix + similarity
# ---------------------------------------------------------------------------


def _interaction_matrix(con, *, lookback_days: int = INTERACTION_LOOKBACK_DAYS) -> tuple[list[str], list[str], np.ndarray]:
    rows = con.execute(
        f"""
        select retailer_id, product_id, count(*) as order_count
        from marts.fact_orders
        where order_ts >= current_timestamp - interval '{lookback_days} days'
        group by retailer_id, product_id
        """
    ).fetchall()
    retailer_ids = sorted({r[0] for r in rows})
    product_ids = sorted({r[1] for r in rows})
    r_idx = {r: i for i, r in enumerate(retailer_ids)}
    p_idx = {p: i for i, p in enumerate(product_ids)}
    matrix = np.zeros((len(retailer_ids), len(product_ids)))
    for retailer_id, product_id, order_count in rows:
        matrix[r_idx[retailer_id], p_idx[product_id]] = float(order_count)
    return retailer_ids, product_ids, matrix


def _embeddings(matrix: np.ndarray, *, method: str, nmf_components: int, seed: int = 42) -> tuple[np.ndarray, np.ndarray, str]:
    """Returns (retailer_vectors, product_vectors, method_used) - vectors to
    run cosine_similarity on for retailer-retailer / product-product
    comparisons respectively."""
    if method == "nmf" and min(matrix.shape) > 1:
        n_components = max(1, min(nmf_components, min(matrix.shape) - 1))
        try:
            model = NMF(n_components=n_components, init="nndsvda", random_state=seed, max_iter=300)
            retailer_vectors = model.fit_transform(matrix)
            product_vectors = model.components_.T
            return retailer_vectors, product_vectors, "nmf"
        except Exception:
            pass  # fall through to cosine-on-raw-matrix
    return matrix, matrix.T, "cosine"


def _top_similar(vectors: np.ndarray, ids: list[str], top_n: int) -> dict[str, list[tuple[str, float]]]:
    if len(ids) < 2:
        return {}
    sims = cosine_similarity(vectors)
    out: dict[str, list[tuple[str, float]]] = {}
    for i, entity_id in enumerate(ids):
        scored = [(ids[j], float(sims[i, j])) for j in range(len(ids)) if j != i and sims[i, j] > 0]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        if scored:
            out[entity_id] = scored[:top_n]
    return out


# ---------------------------------------------------------------------------
# Recommendation types
# ---------------------------------------------------------------------------


def product_similar(con, *, config: MLConfig, model_version: int | None = None) -> list[Recommendation]:
    _retailer_ids, product_ids, matrix = _interaction_matrix(con)
    _retailer_vecs, product_vecs, method = _embeddings(matrix, method=config.recommendation_method, nmf_components=config.nmf_components)
    edges = _top_similar(product_vecs, product_ids, config.recommendation_top_n)
    return _make_rows("product_similar", "product", "product", edges, method=method, model_version=model_version)


def retailer_similar(con, *, config: MLConfig, model_version: int | None = None) -> list[Recommendation]:
    retailer_ids, _product_ids, matrix = _interaction_matrix(con)
    retailer_vecs, _product_vecs, method = _embeddings(matrix, method=config.recommendation_method, nmf_components=config.nmf_components)
    edges = _top_similar(retailer_vecs, retailer_ids, config.recommendation_top_n)
    return _make_rows("retailer_similar", "retailer", "retailer", edges, method=method, model_version=model_version)


def product_frequently_bought_together(con, *, config: MLConfig, model_version: int | None = None) -> list[Recommendation]:
    """Adapted co-purchase signal - see module docstring. Products ordered by
    the same retailer within `co_occurrence_window_days` of each other."""
    rows = con.execute(
        f"""
        select a.product_id as product_a, b.product_id as product_b, count(distinct a.retailer_id) as co_count
        from marts.fact_orders a
        join marts.fact_orders b
          on a.retailer_id = b.retailer_id
          and a.product_id != b.product_id
          and abs(date_diff('day', a.order_ts, b.order_ts)) <= {config.co_occurrence_window_days}
        where a.order_ts >= current_timestamp - interval '{CO_OCCURRENCE_LOOKBACK_DAYS} days'
        group by a.product_id, b.product_id
        """
    ).fetchall()
    edges: dict[str, list[tuple[str, float]]] = {}
    for product_a, product_b, co_count in rows:
        edges.setdefault(product_a, []).append((product_b, float(co_count)))
    for product_a in edges:
        edges[product_a].sort(key=lambda pair: pair[1], reverse=True)
        edges[product_a] = edges[product_a][: config.recommendation_top_n]
    return _make_rows(
        "product_frequently_bought_together", "product", "product", edges, method="co_occurrence",
        model_version=model_version, extra_metadata={"window_days": config.co_occurrence_window_days},
    )


def product_trending(con, *, config: MLConfig, model_version: int | None = None) -> list[Recommendation]:
    window = config.trending_window_days
    rows = con.execute(
        f"""
        select
          o.product_id,
          p.product_category,
          sum(case when o.order_ts >= current_timestamp - interval '{window} days' then 1 else 0 end) as recent_count,
          sum(case when o.order_ts >= current_timestamp - interval '{2 * window} days'
                     and o.order_ts < current_timestamp - interval '{window} days' then 1 else 0 end) as prior_count
        from marts.fact_orders o
        join marts.dim_product p on o.product_id = p.product_id
        where o.order_ts >= current_timestamp - interval '{2 * window} days'
        group by o.product_id, p.product_category
        """
    ).fetchall()

    by_category: dict[str, list[tuple[str, float]]] = {}
    for product_id, category, recent_count, prior_count in rows:
        trend_score = (recent_count - prior_count) / max(prior_count, 1)
        if trend_score <= 0:
            continue
        by_category.setdefault(category or "uncategorized", []).append((product_id, float(trend_score)))

    edges: dict[str, list[tuple[str, float]]] = {}
    for category, scored in by_category.items():
        scored.sort(key=lambda pair: pair[1], reverse=True)
        edges[category] = scored[: config.recommendation_top_n]
    return _make_rows(
        "product_trending", "category", "product", edges, method="trend_ratio",
        model_version=model_version, extra_metadata={"window_days": window},
    )


def retailer_growth(con, *, config: MLConfig, model_version: int | None = None) -> list[Recommendation]:
    window = config.trending_window_days * 2
    rows = con.execute(
        f"""
        select retailer_id, order_date, gmv
        from marts.metrics_retailer_daily
        where order_date >= current_date - interval '{window} days'
        order by retailer_id, order_date
        """
    ).fetchall()

    by_retailer: dict[str, list[float]] = {}
    for retailer_id, _order_date, gmv in rows:
        by_retailer.setdefault(retailer_id, []).append(float(gmv or 0))

    scored: list[tuple[str, float]] = []
    for retailer_id, series in by_retailer.items():
        if len(series) < 3:
            continue
        x_idx = np.arange(len(series))
        slope, _intercept = np.polyfit(x_idx, series, 1)
        if slope > 0:
            scored.append((retailer_id, float(slope)))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    edges = {"marketplace": scored[: config.recommendation_top_n]}
    return _make_rows(
        "retailer_growth", "system", "retailer", edges, method="trend_slope",
        model_version=model_version, extra_metadata={"window_days": window},
    )


def run_all_recommendations(db_path: Path = DUCKDB_PATH, *, config: MLConfig | None = None, model_version: int | None = None) -> list[Recommendation]:
    if not db_path.exists():
        return []
    config = config or load_ml_config()
    recs: list[Recommendation] = []
    with connect_with_retry(db_path, read_only=True) as con:
        for label, builder in (
            ("product_similar", product_similar),
            ("retailer_similar", retailer_similar),
            ("product_frequently_bought_together", product_frequently_bought_together),
            ("product_trending", product_trending),
            ("retailer_growth", retailer_growth),
        ):
            try:
                recs.extend(builder(con, config=config, model_version=model_version))
            except Exception as exc:  # noqa: BLE001 - one recommendation type failing shouldn't block the rest
                print(f"  recommendation group '{label}' failed: {exc!r}")
    return recs


# ---------------------------------------------------------------------------
# Evaluation (for registry bookkeeping - see module docstring on why this
# isn't a promotion-gating quality score)
# ---------------------------------------------------------------------------


def evaluate_recommendations(db_path: Path = DUCKDB_PATH, *, config: MLConfig | None = None) -> dict[str, Any]:
    if not db_path.exists():
        return {"product_coverage": None, "retailer_coverage": None}
    config = config or load_ml_config()
    with connect_with_retry(db_path, read_only=True) as con:
        retailer_ids, product_ids, matrix = _interaction_matrix(con)
        retailer_vecs, product_vecs, _method = _embeddings(matrix, method=config.recommendation_method, nmf_components=config.nmf_components)
        product_edges = _top_similar(product_vecs, product_ids, config.recommendation_top_n)
        retailer_edges = _top_similar(retailer_vecs, retailer_ids, config.recommendation_top_n)
    return {
        "product_coverage": (len(product_edges) / len(product_ids)) if product_ids else None,
        "retailer_coverage": (len(retailer_edges) / len(retailer_ids)) if retailer_ids else None,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists ml")
    con.execute(
        """
        create table if not exists ml.recommendations (
          recommendation_id varchar primary key,
          recommendation_type varchar,
          source_entity_type varchar,
          source_entity_id varchar,
          recommended_entity_type varchar,
          recommended_entity_id varchar,
          score double,
          rank integer,
          method varchar,
          model_name varchar,
          model_version integer,
          generated_at timestamptz,
          metadata varchar
        )
        """
    )


def persist_recommendations(recommendations: list[Recommendation], db_path: Path = DUCKDB_PATH) -> None:
    if not recommendations:
        return
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.executemany(
            """
            insert or replace into ml.recommendations
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.recommendation_id, r.recommendation_type, r.source_entity_type, r.source_entity_id,
                    r.recommended_entity_type, r.recommended_entity_id, r.score, r.rank, r.method,
                    r.model_name, r.model_version, r.generated_at,
                    json.dumps(r.metadata, default=str, sort_keys=True),
                )
                for r in recommendations
            ],
        )
    now = utc_now()
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=f"ml_recommendations_{uuid.uuid4().hex[:8]}",
                source_node="marts.fact_orders",
                target_node="ml.recommendations",
                edge_type="ml_recommendation_generated",
                entity="recommendations",
                created_at=now,
            )
        ],
        db_path,
    )


if __name__ == "__main__":
    active_model_version = None
    try:
        from ml.registry import get_active_model

        active = get_active_model(RECOMMENDATION_MODEL_NAME)
        active_model_version = active.version if active else None
    except Exception:
        pass
    results = run_all_recommendations(model_version=active_model_version)
    persist_recommendations(results)
    print(f"Generated {len(results)} recommendation rows")
