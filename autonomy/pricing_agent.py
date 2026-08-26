"""Pricing Agent (PHASE9-AUTONOMY.md Section 2).

One `PricingAgent` instance decides across every active product in
`context.twin.products` in a single `decide()` call (see `autonomy/
agent_framework.py`'s module docstring for why this differs from a Phase 8
per-entity ABM agent). Three real signals feed the decision, checked in a
fixed priority order per product - a documented rules engine, not a learned
policy (see `agent_framework.py`'s module docstring on scope):

1. A monitoring-confirmed `price_anomaly` (`anomalies.anomaly_events`, surfaced
   on `twin.recent_anomalies` - see `simulation/digital_twin.py`) outranks
   everything else for that product: it's a real detected deviation from a
   real baseline, not a projection, so this agent trusts the anomaly
   detector's `baseline_value` and proposes `revert_price` back to it.
2. Failing that, `reorder_risk_band == "high"` (`marts.compute_product_
   reorder_risk`, also already on `ProductState`) means the product's actual
   current inventory state needs help moving, regardless of any price trend
   - proposes `run_promotion`.
3. Failing both, the ML `price_trend` forecast (`ml.forecasts`, Phase 6 -
   `ml/models/forecasting.py`'s `forecast_price()`) drives `increase_price`/
   `decrease_price` when the forecast drifts meaningfully from the current
   price, or an explicit `freeze_price` hold (still logged, not silence) when
   the drift is inside a noise floor.
   A product with none of the three signals gets no action at all this run -
   there's nothing to decide, so nothing is proposed.

This agent has no fitted price-elasticity model of its own (there isn't one
anywhere in this repo yet - same honest gap Phase 8 already documents on
`simulation/agents/product_agent.py`). Rather than inventing a second,
disconnected elasticity number, it reuses `ProductStrategy.price_elasticity`'s
default (a *linearized* demand-response estimate, `elasticity * delta_pct`,
appended to a price action's `params` purely as an informational readout,
not something this agent optimizes against) - the same constant-elasticity
proxy `product_agent.py`'s digital twin already assumes, so a pricing
decision's stated demand impact is at least consistent with what a
subsequent simulation run over the same twin would itself assume.

Every proposed action still passes through `agent_framework.
enforce_constraints()` before being applied - this module's own
`_price_floor()` (a cost-aware guardrail, distinct from `AgentConstraints.
min_unit_price`'s flat floor) only ever tightens a candidate before that
shared gate runs, never loosens past it.
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
from simulation.agents.product_agent import ProductStrategy
from simulation.digital_twin import ProductState

ACTION_INCREASE_PRICE = "increase_price"
ACTION_DECREASE_PRICE = "decrease_price"
ACTION_RUN_PROMOTION = "run_promotion"
ACTION_FREEZE_PRICE = "freeze_price"
ACTION_REVERT_PRICE = "revert_price"

ACTION_TYPES = (
    ACTION_INCREASE_PRICE, ACTION_DECREASE_PRICE, ACTION_RUN_PROMOTION, ACTION_FREEZE_PRICE, ACTION_REVERT_PRICE,
)

# Ignore forecast drift smaller than this - not worth an action, but still
# worth an explicit freeze_price hold (see _decide_one).
_MEANINGFUL_DELTA_PCT = 0.03

# A pricing-specific business guardrail: never propose a price within this
# fraction of unit_cost. Distinct from AgentConstraints.min_unit_price (a
# flat floor with no cost awareness at all) - only engages when a real
# unit_cost is on hand.
_MARGIN_FLOOR_PCT = 0.05

# Reused, not refit - see module docstring. Pulled from the dataclass default
# rather than hardcoded a second time so the two stay in sync automatically.
_DEFAULT_PRICE_ELASTICITY = ProductStrategy().price_elasticity


@dataclass
class _Candidate:
    product_id: str
    action_type: str
    params: dict[str, Any]
    rationale: str
    confidence: float
    priority: float  # sort key - higher goes first when the run is capped


class PricingAgent(BaseAutonomousAgent):
    agent_type = "pricing"
    table_name = "pricing_actions"  # autonomy.pricing_actions - see agent_framework.persist_actions()

    def decide(self, context: AgentContext) -> list[AgentAction]:
        self.observe(context)
        self.state = AGENT_STATE_DECIDING

        anomaly_by_product = _latest_price_anomaly_by_product(context)
        forecast_by_product = _nearest_price_forecast_by_product(context)

        candidates: list[_Candidate] = []
        for product in context.twin.products.values():
            if product.unit_price is None or product.is_active is False:
                continue
            candidate = self._decide_one(
                product, anomaly_by_product.get(product.product_id), forecast_by_product.get(product.product_id)
            )
            if candidate is not None:
                candidates.append(candidate)

        # Highest-signal candidates win when the run is capped - see
        # AgentConstraints.max_actions_per_agent_per_run.
        candidates.sort(key=lambda c: c.priority, reverse=True)
        selected = candidates[: self.constraints.max_actions_per_agent_per_run]

        return [
            AgentAction(
                action_id=new_action_id(self.agent_type),
                agent_type=self.agent_type,
                action_type=c.action_type,
                entity_type="product",
                entity_id=c.product_id,
                tenant_id=context.tenant_id,
                params=c.params,
                rationale=c.rationale,
                confidence=c.confidence,
                run_id=context.run_id,
            )
            for c in selected
        ]

    def _decide_one(self, product: ProductState, anomaly: Any, forecast: dict[str, Any] | None) -> _Candidate | None:
        price = product.unit_price
        floor = self._price_floor(product)

        # 1. A confirmed anomaly outranks everything else for this product.
        if anomaly is not None and anomaly.baseline_value is not None:
            new_price = max(round(float(anomaly.baseline_value), 2), floor)
            if abs(new_price - price) < 0.01:
                return None
            deviation = anomaly.deviation if anomaly.deviation is not None else 1.0
            return _Candidate(
                product_id=product.product_id,
                action_type=ACTION_REVERT_PRICE,
                params={
                    "current_price": price, "new_price": new_price,
                    "reverted_anomaly_id": anomaly.anomaly_id, "anomaly_deviation": anomaly.deviation,
                },
                rationale=(
                    f"monitoring flagged a price_anomaly on {product.product_id} "
                    f"(current={price}, baseline={new_price}, deviation={anomaly.deviation}) - "
                    f"reverting to the last known-good baseline rather than trusting a value the "
                    f"anomaly detector already distrusts."
                ),
                confidence=min(0.95, max(0.55, abs(deviation) / 3.0)),
                priority=3.0 + abs(deviation),
            )

        # 2. Actual inventory risk beats a forecast-only trend call.
        if product.reorder_risk_band == "high":
            normalized_risk = _normalize_risk_score(product.reorder_risk_score)
            discount = min(0.4, max(0.1, normalized_risk * 0.4))
            new_price = max(round(price * (1.0 - discount), 2), floor)
            if new_price >= price:
                return None
            actual_discount = round(1.0 - (new_price / price), 4) if price else discount
            return _Candidate(
                product_id=product.product_id,
                action_type=ACTION_RUN_PROMOTION,
                params={
                    "current_price": price, "discount": actual_discount, "new_price": new_price,
                    "expected_demand_response_pct": round(_DEFAULT_PRICE_ELASTICITY * -actual_discount, 4),
                },
                rationale=(
                    f"{product.product_id} carries reorder_risk_band=high (score={product.reorder_risk_score}) - "
                    f"running a {actual_discount:.0%} promotion to accelerate sell-through rather than sitting "
                    f"on at-risk inventory."
                ),
                confidence=min(0.9, max(0.4, normalized_risk)),
                priority=2.0 + normalized_risk,
            )

        # 3. Fall back to the ML price_trend forecast.
        if forecast is not None:
            forecast_value = forecast.get("forecast_value")
            if forecast_value is not None and price:
                delta_pct = (float(forecast_value) - price) / price
                if abs(delta_pct) >= _MEANINGFUL_DELTA_PCT:
                    new_price = max(round(float(forecast_value), 2), floor)
                    if abs(new_price - price) < 0.01:
                        return None
                    action_type = ACTION_INCREASE_PRICE if new_price > price else ACTION_DECREASE_PRICE
                    return _Candidate(
                        product_id=product.product_id,
                        action_type=action_type,
                        params={
                            "current_price": price, "new_price": new_price,
                            "forecast_delta_pct": round(delta_pct, 4), "forecast_id": forecast.get("forecast_id"),
                            "expected_demand_response_pct": round(_DEFAULT_PRICE_ELASTICITY * delta_pct, 4),
                        },
                        rationale=(
                            f"ml.forecasts price_trend projects {product.product_id} moving {delta_pct:+.1%} "
                            f"to {forecast_value} by {forecast.get('target_date')} - "
                            f"{'raising' if action_type == ACTION_INCREASE_PRICE else 'lowering'} price toward "
                            f"that projection."
                        ),
                        confidence=min(0.85, max(0.3, abs(delta_pct) * 3)),
                        priority=1.0 + abs(delta_pct),
                    )
                # A forecast exists but the drift is inside the noise floor -
                # an explicit hold, not silence, so the audit trail shows the
                # agent looked and chose not to act.
                return _Candidate(
                    product_id=product.product_id,
                    action_type=ACTION_FREEZE_PRICE,
                    params={
                        "current_price": price, "forecast_value": round(float(forecast_value), 2),
                        "forecast_delta_pct": round(delta_pct, 4),
                    },
                    rationale=(
                        f"ml.forecasts price_trend for {product.product_id} is within "
                        f"+/-{_MEANINGFUL_DELTA_PCT:.0%} of current price ({delta_pct:+.1%}) - holding."
                    ),
                    confidence=0.35,
                    priority=0.1,
                )

        # No anomaly, no inventory risk, no forecast at all this run.
        return None

    def _price_floor(self, product: ProductState) -> float:
        """Never propose a price that guarantees a loss when a real
        unit_cost is on hand - see module docstring."""
        floor = self.constraints.min_unit_price
        if product.unit_cost is not None:
            floor = max(floor, round(product.unit_cost * (1.0 + _MARGIN_FLOOR_PCT), 2))
        return floor


def _normalize_risk_score(reorder_risk_score: float | None) -> float:
    """`marts.compute_product_reorder_risk.reorder_risk_score` (`compute/
    polars/transform_products.py`'s `product_reorder_frame()`) is
    `units_sold*12 + min(100-inventory_count, 100)*0.8` - an unbounded score
    banded at 40/"medium" and 80/"high", NOT a 0..1 value. Every place in
    this module that wants a 0..1 severity reading (a promotion discount
    fraction, a confidence score) normalizes through here first rather than
    using the raw score directly - dividing by 100 puts the "high" band's own
    threshold (80) at 0.8, which is the right order of magnitude for both
    uses below."""
    if reorder_risk_score is None:
        return 0.5
    return min(1.0, max(0.0, reorder_risk_score / 100.0))


def _latest_price_anomaly_by_product(context: AgentContext) -> dict[str, Any]:
    """Most recent open `price_anomaly` per product_id. `twin.recent_anomalies`
    is already newest-first (see `simulation/digital_twin.py`'s
    `_load_anomalies()`), so the first match per entity_id wins."""
    by_product: dict[str, Any] = {}
    for anomaly in context.twin.recent_anomalies:
        if anomaly.anomaly_type != "price_anomaly" or anomaly.entity_type != "product":
            continue
        if anomaly.entity_id not in by_product:
            by_product[anomaly.entity_id] = anomaly
    return by_product


def _nearest_price_forecast_by_product(context: AgentContext) -> dict[str, dict[str, Any]]:
    """Picks, per product, the price_trend forecast row from the most recent
    generation batch with the nearest target_date. `twin.ml_predictions.
    forecasts` holds every horizon step for every forecast_type (`select *
    from ml.forecasts` - see `simulation/digital_twin.py`'s
    `_load_ml_predictions()`), so a product can have several rows here."""
    rows_by_entity: dict[str, list[dict[str, Any]]] = {}
    for row in context.twin.ml_predictions.forecasts:
        if row.get("forecast_type") != "price_trend" or row.get("entity_type") != "product":
            continue
        entity_id = row.get("entity_id")
        if entity_id is None:
            continue
        rows_by_entity.setdefault(entity_id, []).append(row)

    best: dict[str, dict[str, Any]] = {}
    for entity_id, rows in rows_by_entity.items():
        latest_generated_at = max(r.get("generated_at") or "" for r in rows)
        latest_rows = [r for r in rows if (r.get("generated_at") or "") == latest_generated_at]
        best[entity_id] = min(latest_rows, key=lambda r: r.get("target_date") or "")
    return best
