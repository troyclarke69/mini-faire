"""Product agent (PHASE8-SIMULATION.md Section 2, "Product Strategies").

One `ProductAgent` per simulated product. Owns the four strategy dimensions
the spec names - price elasticity, demand response, inventory decay, and a
velocity curve - as a single `expected_demand()` computation per tick, since
in a constant-elasticity demand model those four things are naturally one
formula rather than four independent knobs (see `expected_demand()`'s
docstring for the exact shape). `retailer_agent.py` calls `step()`, which
both converts that tick's demand into a whole-unit order quantity AND
applies inventory decay directly to the twin - the two things a product
genuinely does every tick regardless of which retailer is selling it.

`step()` accumulates fractional demand across calls (`_demand_accumulator`)
rather than rounding `expected_demand()`'s raw float to the nearest whole
unit on every single call. This demo's synthetic order volume typically
puts `expected_demand()` well under 1.0 per retailer-product-tick, so a
scenario/counterfactual mutation that shifts demand by, say, 40% (0.98 ->
1.39) would otherwise round to the *same* integer quantity every tick
(`round(0.98)==round(1.39)==1`) and silently vanish from `total_gmv()` -
exactly what made `demand_shock`/`competitor_entry` scenarios read as an
exact $0.00 GMV delta even though the underlying demand computation was
genuinely, correctly diverging from baseline. Carrying the leftover
fraction forward instead means a persistently elevated (or depressed)
demand signal reliably crosses a whole-unit threshold within a few ticks,
the same way a real fractional arrival rate would, rather than depending on
luck each individual tick. Since a `ProductAgent` can be `step()`-ed by more
than one `RetailerAgent` in the same tick (multiple retailers carrying the
same product - see `scenario_engine.py`'s `build_agents()`), the
accumulator is shared marketplace-wide for this product rather than
per-retailer, consistent with `expected_demand()` itself having no
retailer-specific term.
"""

from __future__ import annotations

from dataclasses import dataclass

from simulation.agents.marketplace_agent import MarketplaceConditions
from simulation.digital_twin import DigitalTwinState


@dataclass
class ProductStrategy:
    # % change in demand per % change in price, relative to reference_price
    # (see ProductAgent.__init__) - negative for a normal good (price up,
    # demand down), matching contracts/order.schema.json's real orders'
    # implicit behavior. -1.0 is unit-elastic; more negative is more
    # price-sensitive.
    price_elasticity: float = -1.2
    base_demand_per_tick: float = 1.0
    # fraction of remaining inventory lost to shrink/spoilage per tick - 0.0
    # is correct for most of this repo's synthetic catalog (durable goods:
    # pour-overs, totes, lamps - see synthetic/generator.py's PRODUCT_NOUN
    # list), left configurable for a scenario that wants to model a
    # perishable-goods retailer instead.
    inventory_decay_rate: float = 0.0
    # how strongly this product's own recent sell-through
    # (digital_twin.py's ProductState.demand_curve_point()) amplifies or
    # dampens next-tick demand, on top of the price/marketplace factors -
    # this is the "velocity curve" the spec names: a product already selling
    # faster than its own baseline tends to keep selling faster (momentum),
    # not just decay back to the baseline every tick.
    velocity_sensitivity: float = 1.0


class ProductAgent:
    def __init__(
        self,
        product_id: str,
        strategy: ProductStrategy,
        reference_price: float | None,
        reference_velocity: float = 0.0,
    ):
        self.product_id = product_id
        self.strategy = strategy
        # Both locked in at construction (the twin's price/velocity at
        # scenario start) so elasticity/momentum are always measured against
        # "what things looked like when this simulation began", not against
        # whatever the current tick just changed them to - otherwise e.g. a
        # price change would have no elasticity effect on the very tick it
        # takes place, and a product with zero sales so far (a fresh
        # simulation) would always read as "velocity collapsed to zero"
        # rather than "no different from where it started".
        self.reference_price = reference_price
        self.reference_velocity = reference_velocity
        # Unclaimed fractional demand carried forward from previous step()
        # calls - see module docstring's "accumulates fractional demand"
        # section. Always starts at 0.0: agents are built fresh per
        # simulation run (never persisted - see scenario_engine.py's
        # build_agents()), so there is no cross-run state to restore.
        self._demand_accumulator: float = 0.0

    def expected_demand(self, twin: DigitalTwinState, conditions: MarketplaceConditions) -> float:
        """Constant-elasticity demand curve: `demand = base * (price /
        reference_price) ** elasticity`, then scaled by this product's own
        velocity momentum (current `demand_curve_point()` vs. this agent's
        `reference_velocity`, both the same units_sold/inventory_count-style
        ratio - see `__init__`'s docstring for why both reference values are
        captured once at construction rather than compared against
        `base_demand_per_tick`, which is a different, absolute-units scale),
        the marketplace's global demand/seasonal multipliers, this product's
        category trend, and dampened by competitor pressure. Returns 0.0 (not
        negative) if price or reference_price is unknown - a tenant-scoped
        twin's ProductState has no pricing at all (see digital_twin.py's
        module docstring), so a product agent built against one always
        reports no demand rather than raising."""
        product = twin.products.get(self.product_id)
        if product is None or product.unit_price is None or not self.reference_price:
            return 0.0

        price_ratio = product.unit_price / self.reference_price
        elasticity_factor = price_ratio**self.strategy.price_elasticity

        velocity_gap = product.demand_curve_point() - self.reference_velocity
        velocity_factor = max(0.0, 1.0 + self.strategy.velocity_sensitivity * velocity_gap)

        category = product.product_category or "uncategorized"
        category_factor = conditions.category_trend.get(category, 1.0)

        competitor_factor = max(0.0, 1.0 - conditions.competitor_pressure)

        demand = (
            self.strategy.base_demand_per_tick
            * elasticity_factor
            * velocity_factor
            * conditions.demand_multiplier
            * conditions.seasonal_multiplier
            * category_factor
            * competitor_factor
        )
        return max(0.0, demand)

    def step(self, twin: DigitalTwinState, conditions: MarketplaceConditions) -> int:
        """Returns a whole-unit order quantity for this call, NOT
        `expected_demand()`'s raw float directly - see module docstring for
        why. `expected_demand()`'s continuous value is added to
        `_demand_accumulator`; whatever whole-unit portion has accumulated
        (0 most calls, occasionally more than 1 if demand has been running
        persistently high) is emitted and subtracted back out, leaving any
        remaining fraction for the next call - so demand is never lost, only
        deferred until enough of it has accumulated to represent a real
        unit."""
        demand = self.expected_demand(twin, conditions)
        self._demand_accumulator += demand
        quantity = int(self._demand_accumulator)
        self._demand_accumulator -= quantity

        if self.strategy.inventory_decay_rate:
            product = twin.products.get(self.product_id)
            if product is not None and product.inventory_count:
                decay_units = int(round(product.inventory_count * self.strategy.inventory_decay_rate))
                if decay_units > 0:
                    twin.apply_inventory_delta(self.product_id, -decay_units)
        return quantity
