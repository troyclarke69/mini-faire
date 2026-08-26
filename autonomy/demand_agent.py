"""Demand Agent (PHASE9-AUTONOMY.md Section 4).

One `DemandAgent` decides across products, retailers, and product/retailer
ML clusters in a single `decide()` call, drawing on two genuinely different
kinds of Phase 6 ML signal rather than one:

- `ml.recommendations` rows (a *dynamic*, recently-measured behavior change -
  `recommendation_type="product_trending"` is a real recent-vs-prior order-
  count ratio per category, `"retailer_growth"` a real GMV trend slope per
  retailer - see `ml/models/recommendations.py`) drive `boost_trending` and
  `launch_promotion`.
- `ml.clusters` rows (a *structural* segment label, stable across runs until
  the underlying features shift - `ml/models/clustering.py`'s
  `_label_cluster()`) drive `target_retailer_segment`, `target_product_
  cluster`, and `suppress_low_margin`.

A trending product gets exactly one action, not two - `launch_promotion`
(mutates the twin's price via the same `new_price`/`discount` shape
`pricing_agent.py`'s `run_promotion` uses, so `agent_framework.
BaseAutonomousAgent._apply_one()` applies it with no override needed) when
margin allows a discount and `pricing_agent.py` isn't already handling that
product's promotion (`reorder_risk_band == "high"`); `boost_trending`
(advisory only - no twin-mutation hook exists in this schema for "increase
visibility" absent a price change) otherwise, so the decision is still
logged even when a real promotion isn't warranted. This deliberately avoids
`pricing_agent.py`'s `run_promotion` (inventory-risk-driven, clears at-risk
stock) and `launch_promotion` (demand-driven, capitalizes on a trend)
stepping on the same product in the same run.

`target_retailer_segment`/`target_product_cluster`/`suppress_low_margin` are
all advisory (no twin-mutation hook exists for "run a segment campaign" or
"suppress marketing spend" either) - same "some decisions are reports, not
mutations" pattern already established by `pricing_agent.py`'s `freeze_price`
and `inventory_agent.py`'s `mark_at_risk`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autonomy.agent_framework import (
    AGENT_STATE_DECIDING,
    AgentAction,
    AgentContext,
    BaseAutonomousAgent,
    new_action_id,
)
from simulation.digital_twin import ProductState, RetailerState

ACTION_LAUNCH_PROMOTION = "launch_promotion"
ACTION_TARGET_RETAILER_SEGMENT = "target_retailer_segment"
ACTION_TARGET_PRODUCT_CLUSTER = "target_product_cluster"
ACTION_BOOST_TRENDING = "boost_trending"
ACTION_SUPPRESS_LOW_MARGIN = "suppress_low_margin"

ACTION_TYPES = (
    ACTION_LAUNCH_PROMOTION, ACTION_TARGET_RETAILER_SEGMENT, ACTION_TARGET_PRODUCT_CLUSTER,
    ACTION_BOOST_TRENDING, ACTION_SUPPRESS_LOW_MARGIN,
)

# product_trending's score is (recent_count - prior_count) / prior_count -
# ignore anything under a 20% order-count increase.
_TRENDING_SCORE_THRESHOLD = 0.2

# A demand-driven promotion's own margin guardrail - same shape and default
# as pricing_agent.py's _MARGIN_FLOOR_PCT/_price_floor (duplicated, not
# imported, since each module's promotion decision has a different trigger
# and this one is a narrow, single-use calculation, not a generic
# agent-framework concern).
_MARGIN_FLOOR_PCT = 0.05
_TREND_PROMOTION_DISCOUNT = 0.1

# ml.clusters segment_name values worth acting on - see
# ml/models/clustering.py's RETAILER_AXES/PRODUCT_AXES for the full label
# vocabulary; segments not listed here (low_gmv, low_velocity,
# anomaly_prone, slow_movers, volatile_inventory, stable, outlier, ...)
# aren't a demand-generation opportunity and get no action.
_GROWTH_RETAILER_SEGMENTS = {"high_velocity", "high_gmv"}
_DEMAND_PRODUCT_SEGMENTS = {"fast_movers", "high_margin"}
_SUPPRESS_PRODUCT_SEGMENTS = {"low_margin"}
_SUPPRESS_MIN_UNITS_SOLD = 5  # only worth suppressing spend on a low-margin product that's actually still selling


@dataclass
class _Candidate:
    entity_type: str
    entity_id: str
    action_type: str
    params: dict[str, Any]
    rationale: str
    confidence: float
    priority: float


class DemandAgent(BaseAutonomousAgent):
    agent_type = "demand"
    table_name = "demand_actions"  # autonomy.demand_actions

    def decide(self, context: AgentContext) -> list[AgentAction]:
        self.observe(context)
        self.state = AGENT_STATE_DECIDING

        candidates: list[_Candidate] = []
        candidates.extend(self._trending_candidates(context))
        candidates.extend(self._retailer_segment_candidates(context))
        candidates.extend(self._product_cluster_candidates(context))

        candidates.sort(key=lambda c: c.priority, reverse=True)
        selected = candidates[: self.constraints.max_actions_per_agent_per_run]

        return [
            AgentAction(
                action_id=new_action_id(self.agent_type),
                agent_type=self.agent_type,
                action_type=c.action_type,
                entity_type=c.entity_type,
                entity_id=c.entity_id,
                tenant_id=context.tenant_id,
                params=c.params,
                rationale=c.rationale,
                confidence=c.confidence,
                run_id=context.run_id,
            )
            for c in selected
        ]

    def _trending_candidates(self, context: AgentContext) -> list[_Candidate]:
        out: list[_Candidate] = []
        for product_id, rec in _best_trending_recommendation_by_product(context).items():
            product = context.twin.products.get(product_id)
            if product is None or product.is_active is False or product.unit_price is None:
                continue
            score = float(rec.get("score") or 0.0)
            if score < _TRENDING_SCORE_THRESHOLD:
                continue

            if product.reorder_risk_band != "high":
                new_price = self._trend_promotion_price(product)
                if new_price is not None:
                    out.append(_Candidate(
                        entity_type="product", entity_id=product_id, action_type=ACTION_LAUNCH_PROMOTION,
                        params={
                            "current_price": product.unit_price, "discount": _TREND_PROMOTION_DISCOUNT,
                            "new_price": new_price, "trend_score": round(score, 4),
                            "category": rec.get("source_entity_id"),
                        },
                        rationale=(
                            f"ml.recommendations product_trending shows {product_id} up {score:+.0%} "
                            f"(recent vs. prior order count) in category={rec.get('source_entity_id')} - "
                            f"launching a {_TREND_PROMOTION_DISCOUNT:.0%} demand-capture promotion while the "
                            f"trend is live."
                        ),
                        confidence=min(0.85, max(0.4, score)),
                        priority=2.0 + score,
                    ))
                    continue

            # Either pricing_agent.py already owns this product's promotion
            # this run (reorder_risk_band=high), or the margin floor left no
            # room for a discount - log the trend as an advisory boost
            # instead of silently dropping it.
            reason = "reorder_risk_band=high (pricing_agent already handling this product's promotion)" \
                if product.reorder_risk_band == "high" else "margin too thin to discount"
            out.append(_Candidate(
                entity_type="product", entity_id=product_id, action_type=ACTION_BOOST_TRENDING,
                params={"trend_score": round(score, 4), "category": rec.get("source_entity_id"), "reason": reason},
                rationale=(
                    f"ml.recommendations product_trending shows {product_id} up {score:+.0%} - "
                    f"flagging for a visibility boost rather than a promotion ({reason})."
                ),
                confidence=min(0.7, max(0.3, score * 0.7)),
                priority=1.0 + score,
            ))
        return out

    def _trend_promotion_price(self, product: ProductState) -> float | None:
        """Same cost-aware guardrail as pricing_agent.py's _price_floor -
        returns None (no promotion possible) rather than a price that
        guarantees a loss."""
        floor = 1.0
        if product.unit_cost is not None:
            floor = max(floor, round(product.unit_cost * (1.0 + _MARGIN_FLOOR_PCT), 2))
        new_price = round(product.unit_price * (1.0 - _TREND_PROMOTION_DISCOUNT), 2)
        if new_price < floor or new_price >= product.unit_price:
            return None
        return new_price

    def _retailer_segment_candidates(self, context: AgentContext) -> list[_Candidate]:
        out: list[_Candidate] = []
        growth_score_by_retailer = _growth_score_by_retailer(context)
        for cluster_row in _latest_clusters(context, entity_type="retailer"):
            retailer_id = cluster_row.get("entity_id")
            retailer: RetailerState | None = context.twin.retailers.get(retailer_id) if retailer_id else None
            if retailer is None or retailer.status not in (None, "active"):
                continue
            segment_name = cluster_row.get("segment_name")
            growth_score = growth_score_by_retailer.get(retailer_id)
            if segment_name not in _GROWTH_RETAILER_SEGMENTS and growth_score is None:
                continue
            confidence = 0.5
            if segment_name in _GROWTH_RETAILER_SEGMENTS:
                confidence += 0.2
            if growth_score is not None:
                confidence += 0.2
            out.append(_Candidate(
                entity_type="retailer", entity_id=retailer_id, action_type=ACTION_TARGET_RETAILER_SEGMENT,
                params={"segment_name": segment_name, "gmv_growth_slope": growth_score},
                rationale=(
                    f"retailer {retailer_id} is ml.clusters segment_name={segment_name}"
                    + (f" with a positive retailer_growth GMV slope ({growth_score:.2f})" if growth_score is not None else "")
                    + " - targeting with a segment demand campaign."
                ),
                confidence=min(0.9, confidence),
                priority=1.0 + (0.5 if segment_name in _GROWTH_RETAILER_SEGMENTS else 0.0) + (0.5 if growth_score is not None else 0.0),
            ))
        return out

    def _product_cluster_candidates(self, context: AgentContext) -> list[_Candidate]:
        out: list[_Candidate] = []
        for cluster_row in _latest_clusters(context, entity_type="product"):
            product_id = cluster_row.get("entity_id")
            product = context.twin.products.get(product_id) if product_id else None
            if product is None or product.is_active is False:
                continue
            segment_name = cluster_row.get("segment_name")

            if segment_name in _DEMAND_PRODUCT_SEGMENTS:
                out.append(_Candidate(
                    entity_type="product", entity_id=product_id, action_type=ACTION_TARGET_PRODUCT_CLUSTER,
                    params={"segment_name": segment_name, "cluster_id": cluster_row.get("cluster_id")},
                    rationale=(
                        f"{product_id} is ml.clusters segment_name={segment_name} - targeting its cluster "
                        f"for cross-sell/demand-generation."
                    ),
                    confidence=0.55,
                    priority=0.8,
                ))
            elif segment_name in _SUPPRESS_PRODUCT_SEGMENTS and product.units_sold >= _SUPPRESS_MIN_UNITS_SOLD:
                out.append(_Candidate(
                    entity_type="product", entity_id=product_id, action_type=ACTION_SUPPRESS_LOW_MARGIN,
                    params={"segment_name": segment_name, "units_sold": product.units_sold},
                    rationale=(
                        f"{product_id} is ml.clusters segment_name=low_margin and still selling "
                        f"({product.units_sold} units this snapshot) - suppressing further demand-generation "
                        f"spend on it rather than promoting a thin-margin mover."
                    ),
                    confidence=0.5,
                    priority=0.7,
                ))
        return out


def _best_trending_recommendation_by_product(context: AgentContext) -> dict[str, dict[str, Any]]:
    """Highest-score `product_trending` recommendation per recommended
    product, from the most recent generation batch. `ml.recommendations`
    rows for `recommendation_type="product_trending"` carry
    `recommended_entity_type="product"`/`recommended_entity_id=<product_id>`
    - see `ml/models/recommendations.py`'s `product_trending()`."""
    rows = [
        r for r in context.twin.ml_predictions.recommendations
        if r.get("recommendation_type") == "product_trending" and r.get("recommended_entity_type") == "product"
    ]
    if not rows:
        return {}
    latest_generated_at = max(r.get("generated_at") or "" for r in rows)
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        if (row.get("generated_at") or "") != latest_generated_at:
            continue
        product_id = row.get("recommended_entity_id")
        if product_id is None:
            continue
        current = best.get(product_id)
        if current is None or float(row.get("score") or 0.0) > float(current.get("score") or 0.0):
            best[product_id] = row
    return best


def _growth_score_by_retailer(context: AgentContext) -> dict[str, float]:
    """`retailer_growth` recommendations - `source_entity_type="system"`,
    `recommended_entity_type="retailer"`, `score` a positive GMV trend
    slope (see `ml/models/recommendations.py`'s `retailer_growth()` - only
    positive slopes are ever persisted, so every row here already indicates
    growth)."""
    rows = [
        r for r in context.twin.ml_predictions.recommendations
        if r.get("recommendation_type") == "retailer_growth" and r.get("recommended_entity_type") == "retailer"
    ]
    if not rows:
        return {}
    latest_generated_at = max(r.get("generated_at") or "" for r in rows)
    return {
        r.get("recommended_entity_id"): float(r.get("score") or 0.0)
        for r in rows
        if (r.get("generated_at") or "") == latest_generated_at and r.get("recommended_entity_id") is not None
    }


def _latest_clusters(context: AgentContext, *, entity_type: str) -> list[dict[str, Any]]:
    """One row per entity for the given entity_type, from the most recent
    computed_at batch. `ml.clusters` rows carry a deterministic `cluster_id`
    per (entity_type, entity_id) - see `ml/models/clustering.py`'s
    `_cluster_id()` - so this is normally already one row per entity, but the
    dedup is explicit rather than assumed."""
    rows = [r for r in context.twin.ml_predictions.clusters if r.get("entity_type") == entity_type]
    if not rows:
        return []
    latest_computed_at = max(r.get("computed_at") or "" for r in rows)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        if (row.get("computed_at") or "") != latest_computed_at:
            continue
        entity_id = row.get("entity_id")
        if entity_id is None or entity_id in seen:
            continue
        seen.add(entity_id)
        out.append(row)
    return out
