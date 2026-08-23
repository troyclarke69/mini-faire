"""Marketplace-level agent (PHASE8-SIMULATION.md Section 2, "Marketplace
Strategies").

One `MarketplaceAgent` per simulation run - there is exactly one marketplace,
unlike retailers/products where there are many. Its job each tick is to
produce a `MarketplaceConditions` snapshot (global demand multiplier,
seasonal multiplier, per-category trend, competitor pressure) that
`product_agent.py`'s `expected_demand()` reads - this is how "global demand
shocks"/"seasonal effects"/"category trends"/"competitor pressure" reach
individual product demand without every product agent needing its own copy
of that logic.

Deliberately NOT a general-purpose ABM framework (no message-passing bus, no
scheduler abstraction) - `simulation/scenario_engine.py` already owns the
tick loop and calls `.step()` on each agent directly in a fixed order
(marketplace, then retailers/products), which is enough structure for what
this phase needs and keeps every agent here plain, seeded, and unit-testable
in isolation.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from simulation.digital_twin import DigitalTwinState


@dataclass
class MarketplaceStrategy:
    demand_shock_probability: float = 0.05
    # multiplier drawn uniformly from this range when a shock fires
    demand_shock_magnitude: tuple[float, float] = (0.5, 1.8)
    seasonal_amplitude: float = 0.15  # +/- fraction, sinusoidal by tick
    seasonal_period_ticks: int = 7
    category_trend_drift: float = 0.02  # per-tick random-walk step size
    competitor_pressure_baseline: float = 0.0  # 0..1, dampens demand/margins


@dataclass
class MarketplaceConditions:
    demand_multiplier: float = 1.0
    seasonal_multiplier: float = 1.0
    category_trend: dict[str, float] = field(default_factory=dict)
    competitor_pressure: float = 0.0


class MarketplaceAgent:
    def __init__(self, strategy: MarketplaceStrategy | None = None, *, seed: int = 0):
        self.strategy = strategy or MarketplaceStrategy()
        self.rng = random.Random(seed)
        self.conditions = MarketplaceConditions()
        self._category_trend: dict[str, float] = {}

    def step(self, twin: DigitalTwinState) -> MarketplaceConditions:
        strategy = self.strategy

        self.conditions.seasonal_multiplier = 1.0 + strategy.seasonal_amplitude * math.sin(
            2 * math.pi * twin.tick / max(1, strategy.seasonal_period_ticks)
        )

        if self.rng.random() < strategy.demand_shock_probability:
            lo, hi = strategy.demand_shock_magnitude
            self.conditions.demand_multiplier = self.rng.uniform(lo, hi)
            twin.event_log.append(
                f"tick={twin.tick} marketplace_demand_shock multiplier={self.conditions.demand_multiplier:.2f}"
            )
        else:
            # decay back toward the neutral 1.0 baseline rather than snapping,
            # so a shock's effect fades over a few ticks instead of vanishing
            # the instant it doesn't re-roll.
            self.conditions.demand_multiplier += (1.0 - self.conditions.demand_multiplier) * 0.3

        categories = {product.product_category or "uncategorized" for product in twin.products.values()}
        for category in categories:
            drift = self._category_trend.get(category, 1.0)
            drift *= 1.0 + self.rng.uniform(-strategy.category_trend_drift, strategy.category_trend_drift)
            self._category_trend[category] = drift
        self.conditions.category_trend = dict(self._category_trend)

        self.conditions.competitor_pressure = strategy.competitor_pressure_baseline
        return self.conditions

    def apply_competitor_entry(self, pressure_increase: float = 0.15) -> None:
        """Used by scenario_engine.py's `competitor_entry` scenario type -
        raises baseline pressure for the remainder of the run."""
        self.strategy.competitor_pressure_baseline = min(1.0, self.strategy.competitor_pressure_baseline + pressure_increase)

    def apply_competitor_exit(self, pressure_decrease: float = 0.15) -> None:
        """Counterpart for `competitor_exit`."""
        self.strategy.competitor_pressure_baseline = max(0.0, self.strategy.competitor_pressure_baseline - pressure_decrease)

    def trigger_demand_shock(self, multiplier: float) -> None:
        """Used by scenario_engine.py's `demand_shock` scenario type to force
        a specific magnitude rather than waiting for the random roll above."""
        self.conditions.demand_multiplier = multiplier
