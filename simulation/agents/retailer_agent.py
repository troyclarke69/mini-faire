"""Retailer agent (PHASE8-SIMULATION.md Section 2, "Retailer Strategies").

One `RetailerAgent` per simulated retailer, holding one product agent per
product this retailer is assumed to carry (see `simulation/scenario_engine.py`
for how that assignment is built from `marts.fact_orders`'s real
retailer/product co-occurrence rather than a fabricated full assortment - no
retailer-catalog table exists in this repo yet, so "which retailer carries
which product" is inferred from order history, not invented).

Each tick, a retailer agent asks its product agents for a whole-unit demand
quantity (product_agent.py's `step()` handles the fractional-demand
accumulation itself - see that module's docstring), caps it by on-hand
inventory, prices it (applying a promotion discount if this tick's
promotion strategy fires), and writes the resulting order onto the twin via
`DigitalTwinState.apply_order()`. The five named strategies below are not
five separate code paths - they're knobs on this one per-tick decision (see
each field's docstring for which part of the decision it changes), matching
`digital_twin.py`'s existing "one coherent state, many read/write angles"
shape rather than a strategy per behavior.

ML-driven strategy is deliberately a strategy the agent can be *told* to use
(`RetailerStrategy.ml_driven=True`) rather than something every agent does
unconditionally - the same "the fitted model output is available where
relevant, not force-injected everywhere" posture Phase 6's
`ml/tenant_models/` already takes. See `_ml_price_adjustment()`'s docstring
for exactly what it reads and how it degrades when no forecast exists.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from simulation.agents.marketplace_agent import MarketplaceConditions
from simulation.agents.product_agent import ProductAgent
from simulation.digital_twin import DigitalTwinState


@dataclass
class RetailerStrategy:
    # pricing_strategy is currently informational/extension-point only
    # ("dynamic" pricing beyond promotions and the ML nudge below isn't
    # separately modeled this phase - documented, not silently assumed);
    # promotion/inventory/fulfillment/anomaly_response below are the knobs
    # RetailerAgent.step() actually reads.
    pricing_strategy: str = "static"  # static | dynamic
    inventory_strategy: str = "reactive"  # reactive | proactive
    promotion_strategy: str = "none"  # none | periodic | anomaly_triggered
    fulfillment_strategy: str = "standard"  # standard | constrained
    anomaly_response_strategy: str = "passive"  # passive | throttle
    ml_driven: bool = False

    promotion_discount: float = 0.15
    promotion_every_n_ticks: int = 7
    reorder_threshold_units: int = 10
    reorder_quantity: int = 50
    fulfillment_cap_per_tick: int = 25


class RetailerAgent:
    def __init__(self, retailer_id: str, strategy: RetailerStrategy, product_agents: list[ProductAgent]):
        self.retailer_id = retailer_id
        self.strategy = strategy
        self.product_agents = product_agents

    def is_operating(self, twin: DigitalTwinState) -> bool:
        retailer = twin.retailers.get(self.retailer_id)
        if retailer is None:
            return False
        # "retailer outage" scenario type sets status to a non-active value
        # via DigitalTwinState.apply_retailer_status() - an outage retailer
        # transacts nothing until status is restored, same as a real POS
        # system being down.
        return retailer.status in (None, "active")

    def _anomaly_is_open(self, twin: DigitalTwinState) -> bool:
        return any(a.entity_id == self.retailer_id for a in twin.recent_anomalies)

    def _ml_price_adjustment(self, twin: DigitalTwinState, product_id: str) -> float:
        """Returns a price multiplier (1.0 = no change) derived from
        `ml.forecasts` if `strategy.ml_driven` is set and a forecast for this
        product exists in the twin snapshot - a rising forecast nudges price
        up slightly, a falling one nudges it down, matching a simple
        "price toward where demand is heading" heuristic. This is
        deliberately not a full pricing-optimization model; it's a bounded
        (+/-5%) nudge on top of whatever pricing_strategy already decided,
        consistent with ml_inference_flow.py's forecasts being read-only
        inputs elsewhere in this repo, never a black box the rest of the app
        can't reason about."""
        if not self.strategy.ml_driven:
            return 1.0
        matching = [
            f for f in twin.ml_predictions.forecasts
            if f.get("entity_id") == product_id and f.get("entity_type") == "product"
        ]
        if not matching:
            return 1.0
        forecast_value = matching[0].get("forecast_value")
        product = twin.products.get(product_id)
        if forecast_value is None or product is None or not product.unit_price:
            return 1.0
        current_velocity = product.demand_curve_point()
        if current_velocity <= 0:
            return 1.0
        # forecast above current pace -> demand is expected to rise -> nudge
        # price up (and vice versa), clipped to +/-5%.
        ratio = forecast_value / max(1e-6, current_velocity)
        nudge = max(-0.05, min(0.05, 0.02 * (ratio - 1.0)))
        return 1.0 + nudge

    def step(self, twin: DigitalTwinState, conditions: MarketplaceConditions, rng: random.Random) -> None:
        if not self.is_operating(twin):
            return
        if self.strategy.anomaly_response_strategy == "throttle" and self._anomaly_is_open(twin):
            twin.event_log.append(f"tick={twin.tick} retailer_throttled retailer={self.retailer_id}")
            return

        promotion_active = (
            self.strategy.promotion_strategy == "periodic"
            and self.strategy.promotion_every_n_ticks > 0
            and twin.tick % self.strategy.promotion_every_n_ticks == 0
            and twin.tick > 0
        ) or (
            self.strategy.promotion_strategy == "anomaly_triggered" and self._anomaly_is_open(twin)
        )

        for product_agent in self.product_agents:
            product = twin.products.get(product_agent.product_id)
            if product is None or product.unit_price is None:
                continue

            # product_agent.step() already returns a whole-unit quantity
            # (its own fractional demand accumulator, not a per-call
            # round() - see product_agent.py's module docstring for why),
            # so no rounding happens here.
            quantity = product_agent.step(twin, conditions)
            if quantity <= 0:
                continue
            if product.inventory_count is not None:
                quantity = min(quantity, product.inventory_count)
            if quantity <= 0:
                continue

            price = product.unit_price
            if promotion_active:
                price *= 1.0 - self.strategy.promotion_discount
            price *= self._ml_price_adjustment(twin, product_agent.product_id)

            # fulfillment_strategy models how cleanly demand converts to a
            # completed sale - "standard" fulfills everything that fits in
            # stock (already capped above); "constrained" simulates a
            # fulfillment bottleneck (e.g. understaffed warehouse) capping
            # what actually ships regardless of available inventory.
            if self.strategy.fulfillment_strategy == "constrained":
                quantity = max(0, min(quantity, self.strategy.fulfillment_cap_per_tick))
                if quantity <= 0:
                    continue

            gross_amount = round(price * quantity, 2)
            twin.apply_order(self.retailer_id, product_agent.product_id, quantity, gross_amount, gross_amount)

            if self.strategy.inventory_strategy == "proactive" and product.inventory_count is not None:
                if product.inventory_count <= self.strategy.reorder_threshold_units:
                    twin.apply_inventory_delta(product_agent.product_id, self.strategy.reorder_quantity)
                    twin.event_log.append(
                        f"tick={twin.tick} reorder retailer={self.retailer_id} "
                        f"product={product_agent.product_id} qty={self.strategy.reorder_quantity}"
                    )
