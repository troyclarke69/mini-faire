"""Autonomous Agent Framework (PHASE9-AUTONOMY.md Section 1).

Scope translation, same posture as `simulation/digital_twin.py`'s module
docstring: the spec's `warehouse/autonomy/<x>_actions` paths are a
filesystem-path shape that doesn't fit how this repo actually stores
warehouse output. Every domain lives in its own DuckDB schema (`marts.*`,
`ml.*`, `tenant.*`, `anomalies.*`, `monitoring.*`, `simulation.*`) - this
phase adds an `autonomy` schema instead, with one table per agent type
(`autonomy.pricing_actions`, `autonomy.inventory_actions`,
`autonomy.demand_actions`, `autonomy.anomaly_actions`,
`autonomy.retailer_strategy_actions`), all sharing the identical
`AgentAction` shape below and the persistence helpers at the bottom of this
module.

A Phase 9 "autonomous agent" is a materially different thing from a Phase 8
`simulation/agents/*.py` ABM agent, despite both being called "agents" and
both living one layer below `simulation`/`autonomy` orchestration:

- A Phase 8 ABM agent (`RetailerAgent`/`ProductAgent`/`MarketplaceAgent`) is
  built fresh, per simulation run, one instance per entity, and exists to
  model *emergent, bottom-up* marketplace behavior for that run only -
  never persisted, never makes a "should I do this" judgment call, just
  reacts to its strategy dataclass every tick.
- A Phase 9 autonomous agent (this module's `BaseAutonomousAgent`
  subclasses) is one instance PER AGENT TYPE, decides ACROSS every relevant
  entity in the twin in one `decide()` call, reads real ML predictions/
  anomaly history/monitoring health to make a business judgment call, and
  its decisions are meant to be genuinely applied to a live digital twin
  and persisted as an audit trail - much closer to a rules-engine "should
  we act on this" layer than to an ABM simulation participant.

The two do connect concretely, not just conceptually: `autonomy/
retailer_strategy_agent.py`'s decisions are literally Phase 8 `RetailerStrategy`
dataclass field changes, threaded through `scenario_engine.run_scenario()`'s
existing `retailer_strategy_overrides` parameter; `autonomy/
anomaly_response_agent.py`'s `trigger_simulation_scenario`/
`trigger_counterfactual_analysis` actions literally call
`scenario_engine.run_scenario()`/`counterfactuals.run_counterfactual()`.
See each agent module's docstring for the specific wiring.

No reinforcement learning, multi-agent negotiation protocol, or learned
policy lives here - `score_reward()` below is a real, measured signal (a
baseline-projection GMV delta - see its docstring), not a training target,
and conflict resolution (`orchestration/agent_flow.py`) is a fixed,
documented priority order, not a learned or negotiated outcome. Both are
explicitly PHASE9-AUTONOMY.md's own stated boundary: "Phase 10 - Full
Marketplace Optimizer... using RL, multi-agent coordination, and global
objective functions" is where that lives, not here.
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingestion.duckdb_utils import connect_with_retry
from ingestion.paths import DUCKDB_PATH
from simulation.digital_twin import DigitalTwinState

# -- Agent lifecycle states (PHASE9-AUTONOMY.md Section 1's "agent
# lifecycle") - tracked on each agent instance across one decide()/act()
# pass, and surfaced to the frontend via api/autonomy_api.py's /autonomy/state.
AGENT_STATE_IDLE = "idle"
AGENT_STATE_OBSERVING = "observing"
AGENT_STATE_DECIDING = "deciding"
AGENT_STATE_ACTING = "acting"
AGENT_STATE_COOLDOWN = "cooldown"

AGENT_STATES = (
    AGENT_STATE_IDLE, AGENT_STATE_OBSERVING, AGENT_STATE_DECIDING, AGENT_STATE_ACTING, AGENT_STATE_COOLDOWN,
)

# -- AgentAction.status values
ACTION_STATUS_PROPOSED = "proposed"
ACTION_STATUS_APPLIED = "applied"
ACTION_STATUS_REJECTED = "rejected"
ACTION_STATUS_REVERTED = "reverted"

ACTION_STATUSES = (ACTION_STATUS_PROPOSED, ACTION_STATUS_APPLIED, ACTION_STATUS_REJECTED, ACTION_STATUS_REVERTED)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_action_id(agent_type: str) -> str:
    return f"action_{agent_type}_{uuid.uuid4().hex[:12]}"


@dataclass
class AgentConstraints:
    """Safety limits every agent's `BaseAutonomousAgent.enforce_constraints()`
    call respects - PHASE9-AUTONOMY.md Section 1's "agent constraints"/
    "safety limits" bullets. Shared across all five agent types (one
    dataclass, not five bespoke checks) since the actual limits - don't move
    price too far in a single action, don't reorder an absurd multiple of
    normal stock, don't let one run take unbounded actions, don't act on the
    same entity twice in one run - are the same shape regardless of which
    agent is proposing the action; only the values might reasonably differ
    per deployment, which is why this is a dataclass with defaults rather
    than a hardcoded check per agent module."""

    max_price_change_pct: float = 0.25  # a single pricing-type action may move price at most +/-25% from its current value
    min_unit_price: float = 1.0  # no action may drop a price below this floor
    max_reorder_multiplier: float = 3.0  # a reorder action may request at most this many x a product's normal reorder_quantity
    max_actions_per_agent_per_run: int = 8  # caps how many actions one agent may propose in a single decide() call
    max_promotion_discount: float = 0.4  # a promotion-triggering action may discount at most this fraction


@dataclass
class AgentAction:
    """One proposed (and, once resolved, applied/rejected/reverted) agent
    decision - the row shape persisted to `autonomy.<agent_type>_actions`
    (see `persist_actions()` below) and returned over `api/autonomy_api.py`.
    `params` is free-form per `action_type` (see each agent module's
    `ACTION_*` constants for what each one carries) and is stored as a
    `json.dumps()` string in a `varchar` column when persisted - same
    "JSON blob in a varchar column" convention `simulation.scenario_results.
    params`/`ml.model_registry.params`/`multi_tenant.tenants.metadata`
    already use. A fresh action from `decide()` carries `params` as a real
    dict; an action read back via `list_actions()` carries it as that same
    JSON string, unparsed - the API layer decides whether to parse it before
    handing it to the frontend, same split `simulation_api.py` already
    documents for scenario/counterfactual results."""

    action_id: str
    agent_type: str  # "pricing" | "inventory" | "demand" | "anomaly_response" | "retailer_strategy"
    action_type: str  # e.g. "increase_price", "reorder_inventory" - see each agent module's ACTION_* constants
    entity_type: str  # "product" | "retailer" | "marketplace"
    entity_id: str
    tenant_id: str | None
    params: dict[str, Any]
    rationale: str
    confidence: float  # 0..1 - this agent's own confidence in the decision, not a calibrated probability
    status: str = ACTION_STATUS_PROPOSED
    reward: float | None = None
    run_id: str | None = None
    created_at: str = field(default_factory=lambda: _utc_now().isoformat())
    applied_at: str | None = None


@dataclass
class AgentContext:
    """Bundles everything a `decide()` call needs to read, built once per
    `orchestration/agent_flow.py` run and shared read-only across all five
    agents - PHASE9-AUTONOMY.md Section 1's "agents must integrate with ML
    predictions / anomaly engine / monitoring metrics / digital-twin state /
    simulation engine". `twin` already carries `ml_predictions` (forecasts/
    clusters/recommendations/anomaly_classifications) and `recent_anomalies`
    - see `simulation/digital_twin.py`'s `load_digital_twin()` - so most
    agents only need `context.twin`; `pipeline_healthy` is the one signal
    that comes from a genuinely different schema (`monitoring.system_metrics`
    - see `orchestration/agent_flow.py`'s `_pipeline_health_check()`) and so
    is threaded in separately rather than bolted onto `DigitalTwinState`,
    which is Phase 8's type, not this phase's to extend."""

    twin: DigitalTwinState
    tenant_id: str | None
    run_id: str
    seed: int
    pipeline_healthy: bool
    cooldown_entities: set[tuple[str, str]] = field(default_factory=set)


class BaseAutonomousAgent(ABC):
    """One instance per agent TYPE, not per entity - see module docstring
    for why this differs from Phase 8's per-entity ABM agents. Subclasses
    implement `decide()`; this base class owns the shared lifecycle/safety/
    application machinery so every agent module only has to write its own
    decision logic, not its own bookkeeping."""

    agent_type: str = "base"

    def __init__(self, constraints: AgentConstraints | None = None):
        self.constraints = constraints or AgentConstraints()
        self.state = AGENT_STATE_IDLE

    def observe(self, context: AgentContext) -> None:
        """Lifecycle hook - transitions to OBSERVING. Kept as its own step
        (rather than folding into `decide()`) because PHASE9-AUTONOMY.md
        Section 1 names "agent lifecycle" and "agent decision loop" as two
        separate things this framework must define, and so a future agent
        that needs to build up scratch state before deciding (e.g. a
        streaming agent watching a rolling window across calls) has a real
        place to do that without every subclass reinventing it."""
        self.state = AGENT_STATE_OBSERVING

    @abstractmethod
    def decide(self, context: AgentContext) -> list[AgentAction]:
        """Returns proposed actions (status=PROPOSED). MUST NOT mutate
        `context.twin` - mutation only happens in `act()`, after
        `orchestration/agent_flow.py`'s conflict-resolution pass has had a
        chance to drop or adjust a proposal. Subclasses should call
        `self.observe(context)` then set `self.state = AGENT_STATE_DECIDING`
        at the start of their own implementation, and cap their own output
        at `self.constraints.max_actions_per_agent_per_run`."""
        raise NotImplementedError

    def enforce_constraints(self, action: AgentAction, context: AgentContext) -> AgentAction | None:
        """Shared safety-limit gate every proposed action passes through
        before `orchestration/agent_flow.py` applies it - PHASE9-AUTONOMY.md
        Section 1's "agent constraints"/"safety limits", applied uniformly
        rather than each agent re-implementing its own bounds-checking.
        Returns the action unchanged if already within limits, a clamped
        copy if a numeric param needed capping, or None if the action must
        be rejected outright (an entity already acted on this run - see
        `AgentContext.cooldown_entities`, populated by `orchestration/
        agent_flow.py`'s conflict resolution, not by this method). Subclasses
        may override to layer agent-specific checks on top, but should call
        `super().enforce_constraints()` first rather than skipping it."""
        if (action.entity_type, action.entity_id) in context.cooldown_entities:
            return None

        params = dict(action.params)

        if "new_price" in params and "current_price" in params:
            try:
                current = float(params["current_price"])
                proposed = float(params["new_price"])
            except (TypeError, ValueError):
                current = proposed = None
            if current is not None and proposed is not None and current > 0:
                max_delta = current * self.constraints.max_price_change_pct
                proposed = max(current - max_delta, min(current + max_delta, proposed))
                proposed = max(proposed, self.constraints.min_unit_price)
                params["new_price"] = round(proposed, 2)

        if "quantity" in params and "base_reorder_quantity" in params:
            try:
                base = float(params["base_reorder_quantity"]) or 1.0
                quantity = float(params["quantity"])
            except (TypeError, ValueError):
                base = quantity = None
            if base is not None and quantity is not None:
                cap = base * self.constraints.max_reorder_multiplier
                if quantity > cap:
                    capped = int(round(cap))
                    params["quantity"] = capped
                    # Keep the real twin-mutation field (see _apply_one()
                    # below) in sync with the just-capped planning field, so
                    # the cap actually reaches what gets applied to the twin,
                    # not just the audit-trail "quantity" reading -
                    # autonomy/inventory_agent.py's reorder_inventory action
                    # sets both keys to the same value for exactly this
                    # reason.
                    if "inventory_delta" in params:
                        params["inventory_delta"] = capped

        if "discount" in params:
            try:
                discount = float(params["discount"])
            except (TypeError, ValueError):
                discount = None
            if discount is not None and discount > self.constraints.max_promotion_discount:
                params["discount"] = self.constraints.max_promotion_discount

        return AgentAction(
            action_id=action.action_id, agent_type=action.agent_type, action_type=action.action_type,
            entity_type=action.entity_type, entity_id=action.entity_id, tenant_id=action.tenant_id,
            params=params, rationale=action.rationale, confidence=action.confidence,
            status=action.status, reward=action.reward, run_id=action.run_id,
            created_at=action.created_at, applied_at=action.applied_at,
        )

    def act(self, twin: DigitalTwinState, actions: list[AgentAction]) -> list[AgentAction]:
        """Applies each already-resolved action's real effect to `twin` via
        `digital_twin.py`'s mutation methods - the only place in this
        framework that actually touches the twin. Unknown/unsupported
        `action_type` values (or ones this base implementation doesn't
        recognize - see `_apply_one()`) are left `status=PROPOSED`, not
        silently marked applied, so a caller can see exactly what did and
        didn't take effect."""
        self.state = AGENT_STATE_ACTING
        applied: list[AgentAction] = []
        for action in actions:
            action.applied_at = _utc_now().isoformat()
            if self._apply_one(twin, action):
                action.status = ACTION_STATUS_APPLIED
            applied.append(action)
        self.state = AGENT_STATE_COOLDOWN
        return applied

    def _apply_one(self, twin: DigitalTwinState, action: AgentAction) -> bool:
        """Maps action_type values shared across multiple agent modules
        (price and inventory mutations - both `pricing_agent.py` and
        `anomaly_response_agent.py` can propose a price change, for
        instance) onto real `DigitalTwinState` mutations. Returns False (a
        legitimate no-op, not an error) for a purely advisory action_type
        that has no twin-state effect by design (e.g.
        `notify_tenant_admins`, `recommend_long_term_change`,
        `mark_at_risk`) - not every autonomous decision changes simulated
        state, some are reports. Subclasses whose action_types need real
        twin mutation beyond price/inventory (retailer status, a strategy
        override that only matters to a *future* scenario run rather than
        the live twin) override this and call `super()._apply_one()` first."""
        if "new_price" in action.params and action.entity_type == "product":
            new_price = action.params.get("new_price")
            if new_price is not None:
                twin.apply_price_change(action.entity_id, float(new_price))
                return True
        if "inventory_delta" in action.params and action.entity_type == "product":
            delta = action.params.get("inventory_delta")
            if delta is not None:
                twin.apply_inventory_delta(action.entity_id, int(delta))
                return True
        return False

    def score_reward(self, action: AgentAction, gmv_before: float, gmv_after: float) -> float:
        """Default reward: the run-level GMV delta a baseline projection
        shows before vs. after this run's resolved actions were applied -
        see `orchestration/agent_flow.py`'s use of `scenario_engine.
        run_baseline_projection()`. Attributed identically to every action
        from the same run, since isolating one action's individual causal
        contribution would require its own counterfactual re-run per action
        (expensive, and `simulation/counterfactuals.py` is built for
        retrospective historical analysis, not a live per-action attribution
        loop) - a documented run-level approximation, not a per-action
        causal isolation. `autonomy/anomaly_response_agent.py` is the one
        agent that overrides this: its `trigger_simulation_scenario`/
        `trigger_counterfactual_analysis` actions have a real, EXACT,
        action-specific reward available already (the triggered scenario/
        counterfactual's own `predicted_gmv_delta`/`counterfactual_gmv_delta`),
        so it uses that instead of the run-level approximation."""
        return round(gmv_after - gmv_before, 2)


# ---------------------------------------------------------------------------
# Shared persistence - identical table shape across all five agent types.
# ---------------------------------------------------------------------------

_ACTION_COLUMNS = (
    "action_id", "agent_type", "action_type", "entity_type", "entity_id", "tenant_id",
    "params", "rationale", "confidence", "status", "reward", "run_id", "created_at", "applied_at",
)


def ensure_actions_table(con, table_name: str) -> None:
    """Creates `autonomy.<table_name>` if missing - identical shape across
    all five agent types (see `AgentAction` above), same defensive-create-
    on-first-use convention every other schema in this repo already uses
    (`monitoring`/`anomalies`/`ml`/`tenant`/`simulation`)."""
    con.execute("create schema if not exists autonomy")
    con.execute(
        f"""
        create table if not exists autonomy.{table_name} (
          action_id varchar primary key, agent_type varchar, action_type varchar,
          entity_type varchar, entity_id varchar, tenant_id varchar,
          params varchar, rationale varchar, confidence double, status varchar,
          reward double, run_id varchar, created_at timestamptz, applied_at timestamptz
        )
        """
    )


def persist_actions(actions: list[AgentAction], table_name: str, db_path: Path = DUCKDB_PATH) -> None:
    """Inserts every action in `actions` into `autonomy.<table_name>` - see
    `ensure_actions_table()`. `params` is stored as a `json.dumps()` string,
    same convention noted on `AgentAction.params` above. A no-op on an empty
    list, matching `ingestion/metadata.py`'s `upsert_lineage_edges()`."""
    if not actions:
        return
    with connect_with_retry(db_path) as con:
        ensure_actions_table(con, table_name)
        con.executemany(
            f"insert into autonomy.{table_name} values ({', '.join(['?'] * len(_ACTION_COLUMNS))})",
            [
                (
                    a.action_id, a.agent_type, a.action_type, a.entity_type, a.entity_id, a.tenant_id,
                    json.dumps(a.params), a.rationale, a.confidence, a.status, a.reward, a.run_id,
                    a.created_at, a.applied_at,
                )
                for a in actions
            ],
        )


def list_actions(
    table_name: str, *, tenant_id: str | None = None, limit: int = 50, db_path: Path = DUCKDB_PATH
) -> list[dict[str, Any]]:
    """Reads back persisted actions, newest first - `api/autonomy_api.py`'s
    per-agent-type GET endpoints. Degrades to `[]` on any failure (missing
    table on a fresh warehouse, etc.), matching `scenario_engine.
    list_scenario_results()`'s exact pattern rather than pre-creating the
    table under a read-only connection (which would fail - DDL needs a
    read-write connection)."""
    sql = f"select * from autonomy.{table_name}"
    params: list[Any] = []
    if tenant_id is not None:
        sql += " where tenant_id = ?"
        params.append(tenant_id)
    sql += " order by created_at desc limit ?"
    params.append(limit)
    try:
        with connect_with_retry(db_path, read_only=True) as con:
            result = con.execute(sql, params)
            cols = [c[0] for c in result.description]
            return [dict(zip(cols, row)) for row in result.fetchall()]
    except Exception:
        return []
