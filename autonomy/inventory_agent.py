"""Inventory Agent (PHASE9-AUTONOMY.md Section 3).

One `InventoryAgent` decides across every product in `context.twin.products`
in a single `decide()` call. Each product gets at most one candidate
decision from a fixed priority ladder - except the stockout case, which
legitimately warrants two distinct actions (a page to a human, and a
concrete restock), since they serve different purposes and one isn't a
substitute for the other:

1. `inventory_count <= 0` AND `reorder_risk_band == "high"` - an actual,
   current stockout, not a projection - proposes BOTH `trigger_stockout_alert`
   (paged through the real `alerts/dispatcher.py` Phase 5 already built - see
   `_apply_one()` override below, not a twin mutation) and `reorder_inventory`
   (a real restock, mutates the twin via the inherited `inventory_delta`
   handling in `agent_framework.BaseAutonomousAgent._apply_one()`).
2. `reorder_risk_band == "high"` with stock still on hand - `reorder_inventory`
   only; not yet page-worthy, but needs restocking now.
3. `reorder_risk_band == "medium"` with a velocity_product forecast
   (`ml.forecasts`, Phase 6) trending upward across its own horizon -
   `increase_reorder_quantity`, a forward-looking planning recommendation
   (no `inventory_delta` - it doesn't touch current stock, just flags that
   the *next* reorder should be sized larger; same "advisory, not applied"
   shape `pricing_agent.py`'s `freeze_price` already established).
4. `reorder_risk_band == "medium"` without that forecast signal -
   `mark_at_risk`, a plain advisory flag (also not applied).
5. `reorder_risk_band == "low"` but genuinely oversupplied (weak sell-through
   relative to a sizeable on-hand count) - `reduce_reorder_quantity`, the
   mirror of #3: a planning recommendation to size the *next* reorder down.
6. Anything else - no signal, no action proposed, same as `pricing_agent.py`.

Two heuristic proxies are documented, not silently assumed, per this
session's established honest-gap posture:

- `marts.compute_product_reorder_risk.reorder_risk_score` (`compute/polars/
  transform_products.py`) is an unbounded score banded at 40/"medium" and
  80/"high" - NOT a 0..1 value. `_normalize_risk_score()` below divides by
  100 before using it as a discount/confidence input, same fix already
  applied in `pricing_agent.py` (duplicated here rather than shared, since
  it's a narrow reorder-risk-domain concept, not a generic agent-framework
  one - both copies stay in sync with the same real formula in their
  docstrings).
- There is no dedicated `reorder_point`/`reorder_quantity` column anywhere in
  this schema. `_base_reorder_quantity()` below approximates a normal restock
  batch as this snapshot's `units_sold` (floored at 10 so a barely-selling
  product still gets a plausible minimum batch) - a documented proxy, not a
  fitted inventory model, same posture as `pricing_agent.py`'s reused
  constant-elasticity assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alerts.dispatcher import dispatch_alert
from autonomy.agent_framework import (
    AGENT_STATE_DECIDING,
    AgentAction,
    AgentContext,
    BaseAutonomousAgent,
    new_action_id,
)
from simulation.digital_twin import DigitalTwinState, ProductState

ACTION_REORDER_INVENTORY = "reorder_inventory"
ACTION_REDUCE_REORDER_QUANTITY = "reduce_reorder_quantity"
ACTION_INCREASE_REORDER_QUANTITY = "increase_reorder_quantity"
ACTION_MARK_AT_RISK = "mark_at_risk"
ACTION_TRIGGER_STOCKOUT_ALERT = "trigger_stockout_alert"

ACTION_TYPES = (
    ACTION_REORDER_INVENTORY, ACTION_REDUCE_REORDER_QUANTITY, ACTION_INCREASE_REORDER_QUANTITY,
    ACTION_MARK_AT_RISK, ACTION_TRIGGER_STOCKOUT_ALERT,
)

# A velocity_product forecast's far-horizon value must exceed its own
# near-horizon value by at least this fraction to count as "rising demand" -
# compared against ITSELF (same forecast_type, same entity, two horizon
# steps), never against digital_twin.py's inventory_velocity/
# demand_curve_point, which is a different scale entirely (see module
# docstring's normalization note - this file makes the same mistake-avoidance
# choice pricing_agent.py's _normalize_risk_score() documents).
_RISING_DEMAND_THRESHOLD = 0.15

# "Oversupplied" for the reduce_reorder_quantity case: on-hand inventory
# above this floor (so a near-empty low-risk product is never flagged) with
# a demand_curve_point() (sell-through rate) below this ceiling.
_EXCESS_INVENTORY_FLOOR = 20
_WEAK_SELL_THROUGH_CEILING = 0.05

_MIN_BASE_REORDER_QUANTITY = 10


@dataclass
class _Candidate:
    product_id: str
    action_type: str
    params: dict[str, Any]
    rationale: str
    confidence: float
    priority: float


class InventoryAgent(BaseAutonomousAgent):
    agent_type = "inventory"
    table_name = "inventory_actions"  # autonomy.inventory_actions

    def decide(self, context: AgentContext) -> list[AgentAction]:
        self.observe(context)
        self.state = AGENT_STATE_DECIDING

        velocity_trend_by_product = _velocity_trend_by_product(context)

        candidates: list[_Candidate] = []
        for product in context.twin.products.values():
            if product.is_active is False:
                continue
            candidates.extend(self._decide_one(product, velocity_trend_by_product.get(product.product_id)))

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

    def _decide_one(self, product: ProductState, velocity_trend_pct: float | None) -> list[_Candidate]:
        band = product.reorder_risk_band
        normalized_risk = _normalize_risk_score(product.reorder_risk_score)
        base_qty = _base_reorder_quantity(product)
        is_stockout = product.inventory_count is not None and product.inventory_count <= 0

        # 1/2. High risk - restock now. Add the stockout page only when
        # there's genuinely nothing left on the shelf.
        if band == "high":
            out: list[_Candidate] = []
            quantity = int(round(base_qty * (1.0 + normalized_risk)))
            reorder = _Candidate(
                product_id=product.product_id,
                action_type=ACTION_REORDER_INVENTORY,
                params={
                    "current_inventory": product.inventory_count, "quantity": quantity,
                    "base_reorder_quantity": base_qty, "inventory_delta": quantity,
                },
                rationale=(
                    f"{product.product_id} carries reorder_risk_band=high "
                    f"(score={product.reorder_risk_score}, on-hand={product.inventory_count}) - "
                    f"reordering {quantity} units (base batch {base_qty}, scaled by risk)."
                ),
                confidence=min(0.95, max(0.5, normalized_risk)),
                priority=(3.9 if is_stockout else 3.0) + normalized_risk,
            )
            if is_stockout:
                out.append(_Candidate(
                    product_id=product.product_id,
                    action_type=ACTION_TRIGGER_STOCKOUT_ALERT,
                    params={
                        "current_inventory": product.inventory_count, "reorder_risk_score": product.reorder_risk_score,
                        "message": f"{product.product_id} ({product.product_name}) is out of stock and reorder_risk_band=high.",
                    },
                    rationale=(
                        f"{product.product_id} is at 0 on-hand inventory with reorder_risk_band=high - "
                        f"paging tenant admins rather than letting it sit unnoticed until the next monitoring pass."
                    ),
                    confidence=0.95,
                    priority=4.0,
                ))
            out.append(reorder)
            return out

        # 3/4. Medium risk - either a proactive reorder-sizing bump (if a
        # real forecast backs it) or a plain "watch this" flag.
        if band == "medium":
            if velocity_trend_pct is not None and velocity_trend_pct >= _RISING_DEMAND_THRESHOLD:
                recommended_qty = int(round(base_qty * (1.0 + velocity_trend_pct)))
                return [_Candidate(
                    product_id=product.product_id,
                    action_type=ACTION_INCREASE_REORDER_QUANTITY,
                    params={
                        "current_inventory": product.inventory_count, "base_reorder_quantity": base_qty,
                        "recommended_reorder_quantity": recommended_qty, "velocity_trend_pct": round(velocity_trend_pct, 4),
                    },
                    rationale=(
                        f"ml.forecasts velocity_product for {product.product_id} rises {velocity_trend_pct:+.1%} "
                        f"across its own horizon while reorder_risk_band=medium - recommending the next reorder "
                        f"batch grow from {base_qty} to {recommended_qty} rather than waiting for band=high."
                    ),
                    confidence=min(0.85, max(0.35, velocity_trend_pct * 2)),
                    priority=2.0 + normalized_risk,
                )]
            return [_Candidate(
                product_id=product.product_id,
                action_type=ACTION_MARK_AT_RISK,
                params={"current_inventory": product.inventory_count, "reorder_risk_score": product.reorder_risk_score},
                rationale=(
                    f"{product.product_id} carries reorder_risk_band=medium (score={product.reorder_risk_score}) "
                    f"with no rising-demand forecast signal - flagging as at-risk to watch, not yet reordering."
                ),
                confidence=0.45,
                priority=1.5 + normalized_risk,
            )]

        # 5. Low risk but genuinely oversupplied.
        if (
            band == "low"
            and product.inventory_count is not None
            and product.inventory_count >= _EXCESS_INVENTORY_FLOOR
            and product.demand_curve_point() <= _WEAK_SELL_THROUGH_CEILING
        ):
            recommended_qty = max(0, int(round(base_qty * 0.5)))
            return [_Candidate(
                product_id=product.product_id,
                action_type=ACTION_REDUCE_REORDER_QUANTITY,
                params={
                    "current_inventory": product.inventory_count, "base_reorder_quantity": base_qty,
                    "recommended_reorder_quantity": recommended_qty,
                    "sell_through_rate": round(product.demand_curve_point(), 4),
                },
                rationale=(
                    f"{product.product_id} is reorder_risk_band=low with {product.inventory_count} on hand and a "
                    f"{product.demand_curve_point():.1%} sell-through rate - recommending the next reorder batch "
                    f"shrink from {base_qty} to {recommended_qty} rather than keep restocking at the same pace."
                ),
                confidence=0.4,
                priority=1.0 + (1.0 - normalized_risk),
            )]

        return []

    def _apply_one(self, twin: DigitalTwinState, action: AgentAction) -> bool:
        """Handles `trigger_stockout_alert` (a real page through Phase 5's
        `alerts/dispatcher.py`, no twin mutation) before falling back to the
        inherited price/inventory handling - `reorder_inventory`'s
        `inventory_delta` key is already handled by
        `BaseAutonomousAgent._apply_one()`, no override needed for that
        action_type."""
        if action.action_type == ACTION_TRIGGER_STOCKOUT_ALERT:
            dispatch_alert(
                "inventory_stockout_risk",
                entity=f"product:{action.entity_id}",
                message=action.params.get("message", f"{action.entity_id} is out of stock."),
                metadata={
                    "current_inventory": action.params.get("current_inventory"),
                    "reorder_risk_score": action.params.get("reorder_risk_score"),
                    "action_id": action.action_id,
                },
                severity="critical",
                lineage_ref=f"autonomy://inventory/{action.action_id}",
            )
            return True
        return super()._apply_one(twin, action)


def _normalize_risk_score(reorder_risk_score: float | None) -> float:
    """See module docstring - `reorder_risk_score` is unbounded, banded at
    40/80, not 0..1. Dividing by 100 puts the "high" threshold (80) at 0.8."""
    if reorder_risk_score is None:
        return 0.5
    return min(1.0, max(0.0, reorder_risk_score / 100.0))


def _base_reorder_quantity(product: ProductState) -> int:
    """See module docstring - no `reorder_point`/`reorder_quantity` column
    exists anywhere in this schema, so this snapshot's `units_sold` stands in
    for "how much this product normally moves", floored so a barely-selling
    product still gets a plausible minimum batch."""
    return max(_MIN_BASE_REORDER_QUANTITY, int(product.units_sold or 0))


def _velocity_trend_by_product(context: AgentContext) -> dict[str, float]:
    """Per product, how much a `velocity_product` forecast (`ml.forecasts`,
    Phase 6) rises from its own near-horizon value to its own far-horizon
    value, within the most recent generation batch - compared only against
    itself, never against `digital_twin.py`'s `inventory_velocity`/
    `demand_curve_point()` (a genuinely different scale - see module
    docstring). Returns `None`-equivalent (key absent) for a product with
    fewer than two horizon steps in its latest batch, since a trend needs
    two points."""
    rows_by_entity: dict[str, list[dict[str, Any]]] = {}
    for row in context.twin.ml_predictions.forecasts:
        if row.get("forecast_type") != "velocity_product" or row.get("entity_type") != "product":
            continue
        entity_id = row.get("entity_id")
        if entity_id is None:
            continue
        rows_by_entity.setdefault(entity_id, []).append(row)

    trend_by_product: dict[str, float] = {}
    for entity_id, rows in rows_by_entity.items():
        latest_generated_at = max(r.get("generated_at") or "" for r in rows)
        latest_rows = [r for r in rows if (r.get("generated_at") or "") == latest_generated_at]
        if len(latest_rows) < 2:
            continue
        latest_rows.sort(key=lambda r: r.get("target_date") or "")
        near_value = latest_rows[0].get("forecast_value")
        far_value = latest_rows[-1].get("forecast_value")
        if near_value is None or far_value is None or not near_value:
            continue
        trend_by_product[entity_id] = (float(far_value) - float(near_value)) / float(near_value)
    return trend_by_product
