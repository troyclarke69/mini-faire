"""Scenario Engine / What-If Simulator (PHASE8-SIMULATION.md Section 3).

Scope translation, stated plainly (see `simulation/digital_twin.py`'s module
docstring for the same posture on Section 1): the spec's
`warehouse/simulation/scenario_results` is a filesystem-path shape that
doesn't fit how this repo actually stores warehouse output - every other
domain lives in a DuckDB schema (`marts.*`, `ml.*`, `tenant.*`,
`anomalies.*`, `monitoring.*`), not a directory tree. This module writes to
a new `simulation.scenario_results` table instead, same convention.

Design: every scenario run is a **baseline vs. treatment** comparison, not a
single forward run read in isolation. `run_scenario()` clones the loaded
twin twice with the *same* random seed - a `baseline` twin that just runs
`ticks` normal steps, and a `scenario` twin that additionally gets the
scenario's setup mutation applied before running the same `ticks` steps.
Comparing the two isolates what the scenario itself changed from ordinary
simulated noise, which is what "predicted GMV" etc. should mean for a
what-if tool (a raw ending number from one run tells you much less).

"Predicted cluster movement" and "predicted recommendations" are
deliberately lightweight proxies, not a re-fit of `ml/models/clustering.py`'s
PCA/KMeans pipeline or `ml/models/recommendations.py`'s collaborative
filtering - retraining either per scenario call would be expensive and
isn't what a fast what-if tool needs. See `_cluster_movement()` and
`_predicted_recommendations()` docstrings for exactly what each computes
instead, and why that's still a real, honest answer to the spec's question
rather than a placeholder.
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DUCKDB_PATH
from simulation.agents.marketplace_agent import MarketplaceAgent, MarketplaceStrategy
from simulation.agents.product_agent import ProductAgent, ProductStrategy
from simulation.agents.retailer_agent import RetailerAgent, RetailerStrategy
from simulation.digital_twin import DigitalTwinState, ProductState, load_digital_twin

SCENARIO_PRICE_CHANGE = "price_change"
SCENARIO_INVENTORY_CHANGE = "inventory_change"
SCENARIO_DEMAND_SHOCK = "demand_shock"
SCENARIO_SUPPLY_CHAIN_DELAY = "supply_chain_delay"
SCENARIO_RETAILER_OUTAGE = "retailer_outage"
SCENARIO_PRODUCT_LAUNCH = "product_launch"
SCENARIO_PROMOTION_EVENT = "promotion_event"
SCENARIO_COMPETITOR_ENTRY = "competitor_entry"
SCENARIO_COMPETITOR_EXIT = "competitor_exit"

SCENARIO_TYPES = (
    SCENARIO_PRICE_CHANGE, SCENARIO_INVENTORY_CHANGE, SCENARIO_DEMAND_SHOCK, SCENARIO_SUPPLY_CHAIN_DELAY,
    SCENARIO_RETAILER_OUTAGE, SCENARIO_PRODUCT_LAUNCH, SCENARIO_PROMOTION_EVENT, SCENARIO_COMPETITOR_ENTRY,
    SCENARIO_COMPETITOR_EXIT,
)

# Each scenario type's required/optional params, surfaced by
# api/simulation_api.py's GET /simulation/scenarios so a frontend form
# (ScenarioBuilder) knows what fields to render without hardcoding a second
# copy of this list.
SCENARIO_PARAM_SCHEMA: dict[str, dict[str, str]] = {
    SCENARIO_PRICE_CHANGE: {"product_id": "required", "new_price": "required"},
    SCENARIO_INVENTORY_CHANGE: {"product_id": "required", "delta": "required"},
    SCENARIO_DEMAND_SHOCK: {"multiplier": "optional (default 1.5)"},
    SCENARIO_SUPPLY_CHAIN_DELAY: {"product_id": "required", "delay_ticks": "optional (default 3)"},
    SCENARIO_RETAILER_OUTAGE: {"retailer_id": "required", "duration_ticks": "optional (default 3)"},
    SCENARIO_PRODUCT_LAUNCH: {
        "product_id": "required", "product_name": "optional", "product_category": "optional",
        "unit_price": "optional (default 25.0)", "inventory_count": "optional (default 100)",
        "retailer_ids": "optional (default: every retailer in the twin)",
    },
    SCENARIO_PROMOTION_EVENT: {
        "retailer_id": "required", "discount": "optional (default 0.2)", "duration_ticks": "optional (default 3)",
    },
    SCENARIO_COMPETITOR_ENTRY: {"pressure_increase": "optional (default 0.15)"},
    SCENARIO_COMPETITOR_EXIT: {"pressure_decrease": "optional (default 0.15)"},
}

DEFAULT_TICKS = 14


class ScenarioError(ValueError):
    """Raised for an unknown scenario_type or missing/invalid params - a
    distinct type so api/simulation_api.py can catch it specifically and
    return a 400, matching TenantError/AuthError/DatabaseError/StorageError's
    existing pattern elsewhere in this repo."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_retailer_product_pairs(tenant_id: str | None, db_path: Path) -> dict[str, list[str]]:
    """retailer_id -> the product_ids it has actually sold, per
    `marts.fact_orders` (or `marts.fact_tenant_orders` for a tenant twin) -
    this repo has no retailer-catalog/assortment table, so "which products
    does this retailer carry" is inferred from real order history rather
    than invented. A retailer with no order history yet (e.g. brand new)
    falls back to every known product in build_agents() below, since an
    empty assortment would make it permanently unsimulatable."""
    if tenant_id is None:
        sql = "select distinct retailer_id, product_id from marts.fact_orders"
        params: list[Any] = []
    else:
        sql = "select distinct retailer_id, product_id from marts.fact_tenant_orders where tenant_id = ?"
        params = [tenant_id]
    try:
        with connect_with_retry(db_path, read_only=True) as con:
            rows = con.execute(sql, params).fetchall()
    except Exception:
        rows = []
    pairs: dict[str, list[str]] = {}
    for retailer_id, product_id in rows:
        pairs.setdefault(retailer_id, []).append(product_id)
    return pairs


def build_agents(
    twin: DigitalTwinState,
    db_path: Path = DUCKDB_PATH,
    *,
    retailer_strategy_overrides: dict[str, RetailerStrategy] | None = None,
    product_strategy_overrides: dict[str, ProductStrategy] | None = None,
    marketplace_strategy: MarketplaceStrategy | None = None,
    seed: int = 0,
) -> tuple[MarketplaceAgent, dict[str, RetailerAgent], dict[str, ProductAgent]]:
    """Builds one agent per retailer/product already in `twin`, wired
    together via real retailer-product order history. Strategy overrides let
    a caller (a scenario, or a future "agent strategy editor" in the
    frontend) customize one retailer/product's behavior without touching the
    rest - unset entries fall back to each strategy dataclass's defaults."""
    pairs = _load_retailer_product_pairs(twin.tenant_id, db_path)

    product_agents: dict[str, ProductAgent] = {}
    for product_id, product in twin.products.items():
        strategy = (product_strategy_overrides or {}).get(product_id) or ProductStrategy()
        product_agents[product_id] = ProductAgent(
            product_id, strategy, reference_price=product.unit_price, reference_velocity=product.demand_curve_point()
        )

    retailer_agents: dict[str, RetailerAgent] = {}
    for retailer_id in twin.retailers:
        carried_ids = pairs.get(retailer_id) or list(twin.products.keys())
        carried_agents = [product_agents[pid] for pid in carried_ids if pid in product_agents]
        strategy = (retailer_strategy_overrides or {}).get(retailer_id) or RetailerStrategy()
        retailer_agents[retailer_id] = RetailerAgent(retailer_id, strategy, carried_agents)

    marketplace_agent = MarketplaceAgent(marketplace_strategy, seed=seed)
    return marketplace_agent, retailer_agents, product_agents


@dataclass
class _TickState:
    """Scratch state a scenario setup hands to the per-tick hook below - e.g.
    "restore this retailer to active at tick N". Deliberately a plain dict
    under the hood (see `_apply_scenario_setup`'s return type) rather than a
    typed dataclass per scenario type, since each scenario needs a different,
    small handful of fields and a dataclass-per-type would be nine dataclasses
    for a handful of optional keys each."""


def _apply_scenario_setup(
    scenario_type: str,
    params: dict[str, Any],
    twin: DigitalTwinState,
    marketplace_agent: MarketplaceAgent,
    retailer_agents: dict[str, RetailerAgent],
    product_agents: dict[str, ProductAgent],
) -> dict[str, Any]:
    """Mutates `twin`/agents in place for the scenario; returns whatever
    scratch state `_apply_scenario_tick_hook()` needs to undo a
    time-boxed effect (an outage/promotion that only lasts `duration_ticks`)."""
    tick_state: dict[str, Any] = {}

    if scenario_type == SCENARIO_PRICE_CHANGE:
        product_id = params["product_id"]
        if product_id not in twin.products:
            raise ScenarioError(f"unknown product_id {product_id!r}")
        twin.apply_price_change(product_id, float(params["new_price"]))

    elif scenario_type == SCENARIO_INVENTORY_CHANGE:
        product_id = params["product_id"]
        if product_id not in twin.products:
            raise ScenarioError(f"unknown product_id {product_id!r}")
        twin.apply_inventory_delta(product_id, int(params["delta"]))

    elif scenario_type == SCENARIO_DEMAND_SHOCK:
        marketplace_agent.trigger_demand_shock(float(params.get("multiplier", 1.5)))

    elif scenario_type == SCENARIO_SUPPLY_CHAIN_DELAY:
        product_id = params["product_id"]
        if product_id not in twin.products:
            raise ScenarioError(f"unknown product_id {product_id!r}")
        delay_ticks = int(params.get("delay_ticks", 3))
        product = twin.products[product_id]
        strategy = product_agents[product_id].strategy if product_id in product_agents else ProductStrategy()
        # Simplified model: a supply delay is treated as immediately losing
        # `delay_ticks` worth of expected replenishment/inventory up front,
        # rather than gating reorders tick-by-tick - see module docstring's
        # framing on why scenario setups favor a clear, inspectable one-time
        # effect over threading extra suppression state through the agents.
        expected_loss = int(round(strategy.base_demand_per_tick * delay_ticks))
        if product.inventory_count is not None and expected_loss > 0:
            twin.apply_inventory_delta(product_id, -min(expected_loss, product.inventory_count))
        twin.event_log.append(f"scenario_setup supply_chain_delay product={product_id} delay_ticks={delay_ticks}")

    elif scenario_type == SCENARIO_RETAILER_OUTAGE:
        retailer_id = params["retailer_id"]
        if retailer_id not in twin.retailers:
            raise ScenarioError(f"unknown retailer_id {retailer_id!r}")
        twin.apply_retailer_status(retailer_id, "outage")
        tick_state["outage_retailer_id"] = retailer_id
        tick_state["outage_restore_tick"] = int(params.get("duration_ticks", 3))

    elif scenario_type == SCENARIO_PRODUCT_LAUNCH:
        product_id = params["product_id"]
        if product_id in twin.products:
            raise ScenarioError(f"product_id {product_id!r} already exists in this twin")
        product = ProductState(
            product_id=product_id,
            product_name=params.get("product_name", product_id),
            product_category=params.get("product_category", "uncategorized"),
            brand_id=params.get("brand_id"),
            unit_price=float(params.get("unit_price", 25.0)),
            unit_cost=params.get("unit_cost"),
            inventory_count=int(params.get("inventory_count", 100)),
            is_active=True,
            units_sold=0,
            inventory_velocity=None,
            reorder_risk_score=None,
            reorder_risk_band=None,
            last_sold_at=None,
        )
        twin.products[product_id] = product
        agent = ProductAgent(product_id, ProductStrategy(), reference_price=product.unit_price, reference_velocity=0.0)
        product_agents[product_id] = agent
        target_ids = params.get("retailer_ids") or list(retailer_agents.keys())
        for retailer_id in target_ids:
            retailer_agent = retailer_agents.get(retailer_id)
            if retailer_agent is not None:
                retailer_agent.product_agents.append(agent)
        twin.event_log.append(f"scenario_setup product_launch product={product_id}")

    elif scenario_type == SCENARIO_PROMOTION_EVENT:
        retailer_id = params["retailer_id"]
        retailer_agent = retailer_agents.get(retailer_id)
        if retailer_agent is None:
            raise ScenarioError(f"unknown retailer_id {retailer_id!r}")
        tick_state["promotion_retailer_id"] = retailer_id
        tick_state["promotion_original_strategy"] = (
            retailer_agent.strategy.promotion_strategy,
            retailer_agent.strategy.promotion_discount,
            retailer_agent.strategy.promotion_every_n_ticks,
        )
        retailer_agent.strategy.promotion_strategy = "periodic"
        retailer_agent.strategy.promotion_discount = float(params.get("discount", 0.2))
        retailer_agent.strategy.promotion_every_n_ticks = 1
        tick_state["promotion_restore_tick"] = int(params.get("duration_ticks", 3))

    elif scenario_type == SCENARIO_COMPETITOR_ENTRY:
        marketplace_agent.apply_competitor_entry(float(params.get("pressure_increase", 0.15)))

    elif scenario_type == SCENARIO_COMPETITOR_EXIT:
        marketplace_agent.apply_competitor_exit(float(params.get("pressure_decrease", 0.15)))

    else:
        raise ScenarioError(f"unknown scenario_type {scenario_type!r} - must be one of {SCENARIO_TYPES}")

    return tick_state


def _apply_scenario_tick_hook(
    twin: DigitalTwinState, retailer_agents: dict[str, RetailerAgent], tick_state: dict[str, Any]
) -> None:
    if "outage_restore_tick" in tick_state and twin.tick >= tick_state["outage_restore_tick"]:
        twin.apply_retailer_status(tick_state["outage_retailer_id"], "active")
        del tick_state["outage_restore_tick"]

    if "promotion_restore_tick" in tick_state and twin.tick >= tick_state["promotion_restore_tick"]:
        retailer_agent = retailer_agents.get(tick_state["promotion_retailer_id"])
        if retailer_agent is not None:
            strategy, discount, every_n = tick_state["promotion_original_strategy"]
            retailer_agent.strategy.promotion_strategy = strategy
            retailer_agent.strategy.promotion_discount = discount
            retailer_agent.strategy.promotion_every_n_ticks = every_n
        del tick_state["promotion_restore_tick"]


def _run_ticks(
    twin: DigitalTwinState,
    marketplace_agent: MarketplaceAgent,
    retailer_agents: dict[str, RetailerAgent],
    ticks: int,
    rng: random.Random,
    tick_state: dict[str, Any] | None = None,
) -> None:
    for _ in range(ticks):
        conditions = marketplace_agent.step(twin)
        for retailer_agent in retailer_agents.values():
            retailer_agent.step(twin, conditions, rng)
        if tick_state:
            _apply_scenario_tick_hook(twin, retailer_agents, tick_state)
        twin.advance_tick()


def run_baseline_projection(
    twin: DigitalTwinState, *, ticks: int = DEFAULT_TICKS, seed: int = 42, db_path: Path = DUCKDB_PATH
) -> dict[str, Any]:
    """Runs `ticks` plain seeded steps forward from a clone of `twin` with no
    scenario mutation applied - "if nothing changes, where do things end up".
    Used by `orchestration/simulation_flow.py` to satisfy PHASE8-SIMULATION.md
    Section 5's "run simulation ticks" bullet as a standalone artifact
    distinct from any individual scenario/counterfactual run (each of which
    already runs its own baseline-vs-treatment tick loop internally, via
    `run_scenario()`/`counterfactuals.run_counterfactual()`); exposed here as
    a real public function rather than making `orchestration/simulation_
    flow.py` reach into this module's private `_run_ticks()` across a
    package boundary (contrast with `simulation/counterfactuals.py`, which
    imports several of this module's private helpers directly - that's a
    sibling module in the same `simulation` package describing the same
    underlying idea, a materially different situation from a cross-package
    import; see that module's docstring)."""
    projection_twin = twin.clone()
    marketplace_agent, retailer_agents, _ = build_agents(projection_twin, db_path, seed=seed)
    _run_ticks(projection_twin, marketplace_agent, retailer_agents, ticks, random.Random(seed))
    return {
        "ticks": ticks,
        "gmv": round(projection_twin.total_gmv(), 2),
        "units_sold": projection_twin.total_units_sold(),
        "average_inventory_velocity": projection_twin.average_inventory_velocity(),
    }


def advance_twin(
    twin: DigitalTwinState, *, ticks: int = 1, seed: int = 42, db_path: Path = DUCKDB_PATH,
    retailer_strategy_overrides: dict[str, RetailerStrategy] | None = None,
) -> None:
    """Advances `twin` forward `ticks` seeded steps IN PLACE - unlike
    `run_baseline_projection()` above, which clones first and leaves the
    original untouched, this mutates the twin the caller already holds.
    Built for `orchestration/agent_flow.py`'s PHASE9-AUTONOMY.md Section 1
    "per simulation tick (digital-twin mode)" run mode: that flow interleaves
    autonomous-agent decision rounds with real marketplace-simulation ticks
    on the *same* evolving twin, so an agent's tick-N decision (e.g. a price
    change) is still in effect - and can compound with organic ABM activity -
    when tick N+1 runs, rather than each tick starting over from a fresh
    clone of the original snapshot. Builds a fresh agent set per call, same
    "agents are never reused across calls" posture as everywhere else in
    this module (see `build_agents()`'s docstring) - this does mean an
    agent's own per-call mutable state (category-trend walk, demand decay)
    does not persist between `advance_twin()` calls, only what actually
    landed on the twin itself does, which is the same "the twin is the only
    durable state" model this whole module already commits to.

    `retailer_strategy_overrides` (see `build_agents()`) is this function's
    one addition beyond a plain "run it forward" call - `autonomy/
    retailer_strategy_agent.py` is the first real caller: unlike every other
    autonomy agent module, a retailer-strategy decision has no `DigitalTwinState`
    field to mutate at all (`RetailerStrategy` lives entirely in this
    module's ABM agent layer, rebuilt fresh every run - see
    `simulation/agents/retailer_agent.py`'s module docstring), so "applying"
    one means actually advancing the twin one tick with the modified
    strategy in effect for that retailer, which is exactly what this
    parameter, added for that purpose, lets a caller do."""
    marketplace_agent, retailer_agents, _ = build_agents(
        twin, db_path, retailer_strategy_overrides=retailer_strategy_overrides, seed=seed
    )
    _run_ticks(twin, marketplace_agent, retailer_agents, ticks, random.Random(seed))


def build_scenario_twin(
    scenario_type: str,
    params: dict[str, Any],
    *,
    tenant_id: str | None = None,
    twin: DigitalTwinState | None = None,
    db_path: Path = DUCKDB_PATH,
) -> DigitalTwinState:
    """Clones (or loads) a twin and applies ONLY a scenario's setup mutation
    - no ticks run. Built for `orchestration/agent_flow.py`'s "scenario"
    run mode (PHASE9-AUTONOMY.md Section 1's "per scenario (Phase 8)"): it
    hands autonomous agents a twin reflecting "what the world looks like
    right after this scenario happened" - the same setup step `run_scenario()`
    applies to its own scenario branch before running it forward - so agents
    can decide/act on top of a hypothetical rather than only ever the live
    warehouse snapshot. Exposed here as a real public entry point rather
    than `orchestration/agent_flow.py` reaching into this module's private
    `_apply_scenario_setup()`/`build_agents()` directly across the package
    boundary - same posture `run_baseline_projection()` already established
    (contrast with `simulation/counterfactuals.py`, a sibling module
    *inside* this package, which does import those privately - see that
    module's docstring)."""
    if scenario_type not in SCENARIO_TYPES:
        raise ScenarioError(f"unknown scenario_type {scenario_type!r} - must be one of {SCENARIO_TYPES}")
    base_twin = twin if twin is not None else load_digital_twin(tenant_id, db_path)
    scenario_twin = base_twin.clone()
    marketplace_agent, retailer_agents, product_agents = build_agents(scenario_twin, db_path, seed=42)
    _apply_scenario_setup(scenario_type, params, scenario_twin, marketplace_agent, retailer_agents, product_agents)
    return scenario_twin


def _retailer_health_score(order_count: int, net_revenue: float, estimated_profit: float | None) -> float:
    """Same formula compute/polars/transform_orders.py's order_health_frame()
    already uses on real data, applied here to a simulated twin's state
    instead - kept identical rather than inventing a second scoring rule."""
    return round(order_count * 10 + net_revenue / 50 + (estimated_profit or 0.0) / 25, 2)


def _predicted_retailer_health(scenario: DigitalTwinState) -> list[dict]:
    return [
        {
            "retailer_id": r.retailer_id,
            "order_count": r.order_count,
            "net_revenue": round(r.net_revenue, 2),
            "retailer_health_score": _retailer_health_score(r.order_count, r.net_revenue, r.estimated_profit),
        }
        for r in scenario.retailers.values()
    ]


def _cluster_movement(baseline: DigitalTwinState, scenario: DigitalTwinState) -> list[dict]:
    """Simplified proxy for "predicted cluster movement" - NOT a re-fit of
    ml/models/clustering.py's PCA/KMeans pipeline (see module docstring).
    Positions each retailer at (order_count, net_revenue) in both runs and
    reports the Euclidean movement between them, sorted descending - "did
    this retailer's behavior profile shift, and by how much" without a
    second model to train and without the sklearn dependency that would
    require."""
    movements = []
    for retailer_id, after in scenario.retailers.items():
        before = baseline.retailers.get(retailer_id)
        if before is None:
            continue
        dx = after.order_count - before.order_count
        dy = after.net_revenue - before.net_revenue
        movements.append(
            {
                "retailer_id": retailer_id,
                "order_count_delta": dx,
                "net_revenue_delta": round(dy, 2),
                "movement_distance": round((dx**2 + dy**2) ** 0.5, 2),
            }
        )
    movements.sort(key=lambda m: m["movement_distance"], reverse=True)
    return movements


def _predicted_recommendations(scenario: DigitalTwinState, *, top_n: int = 5) -> list[dict]:
    """Simplified proxy for "predicted recommendations" - NOT a re-fit of
    ml/models/recommendations.py's collaborative-filtering pipeline (same
    reasoning as `_cluster_movement()`). Ranks products by post-scenario
    sell-through (`ProductState.demand_curve_point()`, the same signal
    recommendations.py itself weighs velocity by) - "what's worth promoting
    given how this scenario played out"."""
    ranked = sorted(scenario.products.values(), key=lambda p: p.demand_curve_point(), reverse=True)[:top_n]
    return [
        {
            "product_id": p.product_id,
            "product_name": p.product_name,
            "demand_curve_point": round(p.demand_curve_point(), 4),
        }
        for p in ranked
    ]


def _predicted_anomalies(baseline: DigitalTwinState, scenario: DigitalTwinState, *, z_threshold: float = 1.5) -> list[dict]:
    """Flags retailers whose scenario-run net_revenue deviates from the
    baseline run by more than `z_threshold` standard deviations of the
    population's deltas - the same z-score idea `anomalies/detector.py` uses
    against real rolling history, applied here to simulated baseline-vs-
    scenario deltas instead (this does not call into anomalies/detector.py
    itself, which scans real warehouse history, not an in-memory simulated
    twin)."""
    deltas = {
        rid: scenario.retailers[rid].net_revenue - baseline.retailers[rid].net_revenue
        for rid in scenario.retailers
        if rid in baseline.retailers
    }
    if len(deltas) < 2:
        return []
    values = list(deltas.values())
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = variance**0.5
    if std == 0:
        return []
    flagged = []
    for retailer_id, delta in deltas.items():
        z = (delta - mean) / std
        if abs(z) >= z_threshold:
            flagged.append({"retailer_id": retailer_id, "net_revenue_delta": round(delta, 2), "z_score": round(z, 2)})
    flagged.sort(key=lambda f: abs(f["z_score"]), reverse=True)
    return flagged


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists simulation")
    con.execute(
        """
        create table if not exists simulation.scenario_results (
          scenario_id varchar primary key,
          scenario_type varchar,
          tenant_id varchar,
          params varchar,
          ticks integer,
          predicted_gmv_baseline double,
          predicted_gmv_scenario double,
          predicted_gmv_delta double,
          predicted_velocity_baseline double,
          predicted_velocity_scenario double,
          predicted_inventory varchar,
          predicted_anomalies varchar,
          predicted_retailer_health varchar,
          predicted_cluster_movement varchar,
          predicted_recommendations varchar,
          started_at timestamptz,
          completed_at timestamptz,
          status varchar
        )
        """
    )


@dataclass
class ScenarioResult:
    scenario_id: str
    scenario_type: str
    tenant_id: str | None
    params: dict[str, Any]
    ticks: int
    predicted_gmv_baseline: float
    predicted_gmv_scenario: float
    predicted_gmv_delta: float
    predicted_velocity_baseline: float | None
    predicted_velocity_scenario: float | None
    predicted_inventory: dict[str, int | None]
    predicted_anomalies: list[dict]
    predicted_retailer_health: list[dict]
    predicted_cluster_movement: list[dict]
    predicted_recommendations: list[dict]
    started_at: str
    completed_at: str
    status: str


def run_scenario(
    scenario_type: str,
    params: dict[str, Any],
    *,
    tenant_id: str | None = None,
    ticks: int = DEFAULT_TICKS,
    seed: int = 42,
    db_path: Path = DUCKDB_PATH,
    twin: DigitalTwinState | None = None,
    retailer_strategy_overrides: dict[str, RetailerStrategy] | None = None,
    product_strategy_overrides: dict[str, ProductStrategy] | None = None,
    persist: bool = True,
) -> ScenarioResult:
    """Runs one what-if scenario end to end: load (or reuse) the twin, clone
    it into a baseline and a scenario branch, apply the scenario's setup only
    to the scenario branch, run both forward `ticks` steps with the same
    seed, diff the results, and (by default) persist to
    `simulation.scenario_results` plus a lineage edge. `twin` lets
    `orchestration/simulation_flow.py` load once and run several scenarios
    against the same starting snapshot rather than re-querying the warehouse
    per scenario. `retailer_strategy_overrides`/`product_strategy_overrides`
    (see `build_agents()`) are applied identically to *both* the baseline and
    scenario branches - overriding a strategy is "what if this retailer
    always behaved this way", a standing property of the run, not part of
    the scenario's setup mutation itself; `api/simulation_api.py`'s
    AgentStrategyEditor-facing endpoint is what constructs these from a
    request body (plain dataclasses in, not raw JSON, to keep this function's
    typing exact)."""
    if scenario_type not in SCENARIO_TYPES:
        raise ScenarioError(f"unknown scenario_type {scenario_type!r} - must be one of {SCENARIO_TYPES}")

    started_at = utc_now()
    base_twin = twin if twin is not None else load_digital_twin(tenant_id, db_path)

    baseline_twin = base_twin.clone()
    scenario_twin = base_twin.clone()

    baseline_marketplace, baseline_retailers, _ = build_agents(
        baseline_twin, db_path,
        retailer_strategy_overrides=retailer_strategy_overrides,
        product_strategy_overrides=product_strategy_overrides,
        seed=seed,
    )
    scenario_marketplace, scenario_retailers, scenario_products = build_agents(
        scenario_twin, db_path,
        retailer_strategy_overrides=retailer_strategy_overrides,
        product_strategy_overrides=product_strategy_overrides,
        seed=seed,
    )

    tick_state = _apply_scenario_setup(
        scenario_type, params, scenario_twin, scenario_marketplace, scenario_retailers, scenario_products
    )

    _run_ticks(baseline_twin, baseline_marketplace, baseline_retailers, ticks, random.Random(seed))
    _run_ticks(scenario_twin, scenario_marketplace, scenario_retailers, ticks, random.Random(seed), tick_state)

    predicted_gmv_baseline = baseline_twin.total_gmv()
    predicted_gmv_scenario = scenario_twin.total_gmv()
    completed_at = utc_now()

    result = ScenarioResult(
        scenario_id=str(uuid.uuid4()),
        scenario_type=scenario_type,
        tenant_id=base_twin.tenant_id,
        params=params,
        ticks=ticks,
        predicted_gmv_baseline=round(predicted_gmv_baseline, 2),
        predicted_gmv_scenario=round(predicted_gmv_scenario, 2),
        predicted_gmv_delta=round(predicted_gmv_scenario - predicted_gmv_baseline, 2),
        predicted_velocity_baseline=baseline_twin.average_inventory_velocity(),
        predicted_velocity_scenario=scenario_twin.average_inventory_velocity(),
        predicted_inventory={pid: p.inventory_count for pid, p in scenario_twin.products.items()},
        predicted_anomalies=_predicted_anomalies(baseline_twin, scenario_twin),
        predicted_retailer_health=_predicted_retailer_health(scenario_twin),
        predicted_cluster_movement=_cluster_movement(baseline_twin, scenario_twin),
        predicted_recommendations=_predicted_recommendations(scenario_twin),
        started_at=started_at,
        completed_at=completed_at,
        status="success",
    )

    if persist:
        persist_scenario_result(result, db_path)
    return result


def persist_scenario_result(result: ScenarioResult, db_path: Path = DUCKDB_PATH) -> None:
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.execute(
            """
            insert into simulation.scenario_results values
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.scenario_id, result.scenario_type, result.tenant_id, json.dumps(result.params, default=str),
                result.ticks, result.predicted_gmv_baseline, result.predicted_gmv_scenario, result.predicted_gmv_delta,
                result.predicted_velocity_baseline, result.predicted_velocity_scenario,
                json.dumps(result.predicted_inventory), json.dumps(result.predicted_anomalies),
                json.dumps(result.predicted_retailer_health), json.dumps(result.predicted_cluster_movement),
                json.dumps(result.predicted_recommendations), result.started_at, result.completed_at, result.status,
            ],
        )
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=f"scenario_{result.scenario_id}",
                source_node="simulation://digital_twin",
                target_node="simulation.scenario_results",
                edge_type="scenario_simulated",
                entity=result.scenario_type,
                created_at=result.completed_at,
            )
        ],
        db_path,
    )


def list_scenario_results(tenant_id: str | None = None, *, limit: int = 50, db_path: Path = DUCKDB_PATH) -> list[dict]:
    try:
        with connect_with_retry(db_path, read_only=True) as con:
            if tenant_id is None:
                result = con.execute(
                    "select * from simulation.scenario_results order by completed_at desc limit ?", [limit]
                )
            else:
                result = con.execute(
                    "select * from simulation.scenario_results where tenant_id = ? order by completed_at desc limit ?",
                    [tenant_id, limit],
                )
            cols = [c[0] for c in result.description]
            return [dict(zip(cols, row)) for row in result.fetchall()]
    except Exception:
        return []


if __name__ == "__main__":
    demo_result = run_scenario(SCENARIO_DEMAND_SHOCK, {"multiplier": 1.8}, persist=False)
    print(json.dumps(demo_result.__dict__, default=str, indent=2))
