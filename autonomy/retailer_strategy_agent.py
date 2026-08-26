"""Retailer Strategy Agent (PHASE9-AUTONOMY.md Section 6).

One `RetailerStrategyAgent` decides across every retailer in
`context.twin.retailers` in a single `decide()` call, picking at most ONE
operational strategy change per retailer per run (see priority ladder below)
plus, independently, an optional `recommend_long_term_change` - the two
never conflict since the recommendation is advisory only (see `_apply_one()`
docstring for why an operational change and an advisory recommendation are
safe to combine but two operational changes for the same retailer are not).

Unlike every other autonomy agent module, a retailer-strategy decision has
NO `DigitalTwinState` field to mutate directly - `RetailerStrategy` lives
entirely in `simulation/agents/retailer_agent.py`'s ABM layer, rebuilt fresh
every simulation run from either its own dataclass defaults or an explicit
override (see that module's docstring). "Applying" a decision here therefore
means something genuinely different from `pricing_agent.py`'s `new_price` or
`inventory_agent.py`'s `inventory_delta`: `_apply_one()` calls
`simulation/scenario_engine.py`'s `advance_twin()` with a
`retailer_strategy_overrides={entity_id: modified_strategy}` (a parameter
added there specifically for this agent - see that function's docstring),
advancing the live twin one real tick with the new strategy in effect, so
the strategy change has a genuine, measurable effect on the twin rather than
being a label attached to nothing.

Because there's no persisted "this retailer's current live strategy"
anywhere in this schema (every simulation run either uses `RetailerStrategy`
defaults or an explicit override, never a stored prior one), each proposed
`strategy_changes` dict is relative to `RetailerStrategy()`'s own defaults,
not to some tracked prior state - a documented simplification, not a
silently assumed one, consistent with this session's established honest-gap
posture (`pricing_agent.py`'s reused elasticity constant, `inventory_agent.py`'s
proxy reorder quantity).

Priority ladder per retailer (highest wins the one operational slot):

1. An open `retailer_health_degradation` anomaly for this retailer
   (`anomalies.anomaly_events`) -> `adjust_inventory_strategy` (proactive
   reordering, earlier threshold) - a real operational fix, deliberately
   different from `anomaly_response_agent.py`'s response to the SAME anomaly
   type (that one runs an investigative `retailer_outage` scenario; this one
   changes how the retailer actually restocks going forward).
2. `estimated_profit < 0` (the retailer is currently losing money on the
   twin's own numbers) -> `adjust_fulfillment_strategy` (throttle to
   `constrained`, lower `fulfillment_cap_per_tick`) - cut cost exposure.
3. `retailer_health_score` below a "concerning" floor -> `adjust_pricing_strategy`
   (turn on `ml_driven`/`pricing_strategy="dynamic"`, letting Phase 6's ML
   forecasts help rather than a static price).
4. `ml.clusters` segment_name in `{"low_velocity", "low_gmv"}` (a structural
   Phase 6 label, not a single-tick signal) -> `adjust_promotion_strategy`
   (switch to `periodic` promotions, raise the discount).

`recommend_long_term_change` is independent of the ladder above: triggered
by `segment_name == "anomaly_prone"` (a persistent, structural trait a
single-tick strategy tweak won't fix) - advisory only, same "some decisions
are reports" pattern `pricing_agent.py`'s `freeze_price` and
`inventory_agent.py`'s `mark_at_risk` already established.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autonomy.agent_framework import (
    AGENT_STATE_DECIDING,
    AgentAction,
    AgentContext,
    BaseAutonomousAgent,
    new_action_id,
)
from ingestion.paths import DUCKDB_PATH
from simulation.agents.retailer_agent import RetailerStrategy
from simulation.digital_twin import DigitalTwinState

ACTION_ADJUST_PRICING_STRATEGY = "adjust_pricing_strategy"
ACTION_ADJUST_INVENTORY_STRATEGY = "adjust_inventory_strategy"
ACTION_ADJUST_PROMOTION_STRATEGY = "adjust_promotion_strategy"
ACTION_ADJUST_FULFILLMENT_STRATEGY = "adjust_fulfillment_strategy"
ACTION_RECOMMEND_LONG_TERM_CHANGE = "recommend_long_term_change"

ACTION_TYPES = (
    ACTION_ADJUST_PRICING_STRATEGY, ACTION_ADJUST_INVENTORY_STRATEGY, ACTION_ADJUST_PROMOTION_STRATEGY,
    ACTION_ADJUST_FULFILLMENT_STRATEGY, ACTION_RECOMMEND_LONG_TERM_CHANGE,
)

# The set of action_types _apply_one() realizes via advance_twin() - every
# other action_type (just recommend_long_term_change) is advisory only.
_STRATEGY_MUTATION_ACTIONS = {
    ACTION_ADJUST_PRICING_STRATEGY, ACTION_ADJUST_INVENTORY_STRATEGY,
    ACTION_ADJUST_PROMOTION_STRATEGY, ACTION_ADJUST_FULFILLMENT_STRATEGY,
}

_HEALTH_CONCERNING_FLOOR = 0.4
_PROACTIVE_REORDER_THRESHOLD_FRACTION = 0.7  # vs. RetailerStrategy's default reorder_threshold_units=10
_CONSTRAINED_FULFILLMENT_CAP_FRACTION = 0.6  # vs. RetailerStrategy's default fulfillment_cap_per_tick=25
_PROMOTION_STRATEGY_DISCOUNT = 0.25  # vs. RetailerStrategy's default promotion_discount=0.15

_GROWTH_RETAILER_STALL_SEGMENTS = {"low_velocity", "low_gmv"}
_LONG_TERM_REVIEW_SEGMENT = "anomaly_prone"


@dataclass
class _Candidate:
    entity_id: str
    action_type: str
    params: dict[str, Any]
    rationale: str
    confidence: float
    priority: float


class RetailerStrategyAgent(BaseAutonomousAgent):
    agent_type = "retailer_strategy"
    table_name = "retailer_strategy_actions"  # autonomy.retailer_strategy_actions

    def __init__(self, *args: Any, db_path: Path = DUCKDB_PATH, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # Only _apply_one() needs this - see module docstring on why
        # applying a decision here means a real advance_twin() call.
        self.db_path = db_path

    def decide(self, context: AgentContext) -> list[AgentAction]:
        self.observe(context)
        self.state = AGENT_STATE_DECIDING

        anomaly_retailer_ids = {
            a.entity_id for a in context.twin.recent_anomalies if a.anomaly_type == "retailer_health_degradation"
        }
        segment_by_retailer = _latest_retailer_segments(context)

        candidates: list[_Candidate] = []
        for retailer_id, retailer in context.twin.retailers.items():
            if retailer.status not in (None, "active"):
                continue
            segment_name = segment_by_retailer.get(retailer_id)

            operational = self._decide_operational(retailer_id, retailer, retailer_id in anomaly_retailer_ids, segment_name)
            if operational is not None:
                candidates.append(operational)

            if segment_name == _LONG_TERM_REVIEW_SEGMENT:
                candidates.append(_Candidate(
                    entity_id=retailer_id, action_type=ACTION_RECOMMEND_LONG_TERM_CHANGE,
                    params={"segment_name": segment_name},
                    rationale=(
                        f"retailer {retailer_id} is ml.clusters segment_name=anomaly_prone - a persistent, "
                        f"structural pattern a single-tick strategy tweak won't fix; recommending a longer-term "
                        f"relationship review rather than another tactical adjustment."
                    ),
                    confidence=0.5,
                    priority=0.5,
                ))

        candidates.sort(key=lambda c: c.priority, reverse=True)
        selected = candidates[: self.constraints.max_actions_per_agent_per_run]

        return [
            AgentAction(
                action_id=new_action_id(self.agent_type),
                agent_type=self.agent_type,
                action_type=c.action_type,
                entity_type="retailer",
                entity_id=c.entity_id,
                tenant_id=context.tenant_id,
                params={**c.params, "seed": context.seed},
                rationale=c.rationale,
                confidence=c.confidence,
                run_id=context.run_id,
            )
            for c in selected
        ]

    def _decide_operational(self, retailer_id: str, retailer: Any, has_health_anomaly: bool, segment_name: str | None) -> _Candidate | None:
        if has_health_anomaly:
            new_threshold = max(1, int(round(RetailerStrategy().reorder_threshold_units * _PROACTIVE_REORDER_THRESHOLD_FRACTION)))
            return _Candidate(
                entity_id=retailer_id, action_type=ACTION_ADJUST_INVENTORY_STRATEGY,
                params={"strategy_changes": {"inventory_strategy": "proactive", "reorder_threshold_units": new_threshold}},
                rationale=(
                    f"anomalies.anomaly_events flagged retailer_health_degradation on {retailer_id} - switching "
                    f"to a proactive inventory_strategy (reorder_threshold_units={new_threshold}) so restocking "
                    f"happens earlier while health recovers."
                ),
                confidence=0.75, priority=3.0,
            )

        if retailer.estimated_profit is not None and retailer.estimated_profit < 0:
            new_cap = max(1, int(round(RetailerStrategy().fulfillment_cap_per_tick * _CONSTRAINED_FULFILLMENT_CAP_FRACTION)))
            return _Candidate(
                entity_id=retailer_id, action_type=ACTION_ADJUST_FULFILLMENT_STRATEGY,
                params={"strategy_changes": {"fulfillment_strategy": "constrained", "fulfillment_cap_per_tick": new_cap}},
                rationale=(
                    f"{retailer_id} shows estimated_profit={retailer.estimated_profit:.2f} (currently a loss) - "
                    f"throttling to a constrained fulfillment_strategy (cap={new_cap}/tick) to cut cost exposure."
                ),
                confidence=0.6, priority=2.5,
            )

        if retailer.retailer_health_score is not None and retailer.retailer_health_score < _HEALTH_CONCERNING_FLOOR:
            return _Candidate(
                entity_id=retailer_id, action_type=ACTION_ADJUST_PRICING_STRATEGY,
                params={"strategy_changes": {"pricing_strategy": "dynamic", "ml_driven": True}},
                rationale=(
                    f"{retailer_id} shows retailer_health_score={retailer.retailer_health_score:.2f} (below "
                    f"{_HEALTH_CONCERNING_FLOOR}) - switching to ML-driven dynamic pricing rather than a static "
                    f"price while health is concerning."
                ),
                confidence=min(0.8, max(0.3, _HEALTH_CONCERNING_FLOOR - retailer.retailer_health_score + 0.3)),
                priority=2.0,
            )

        if segment_name in _GROWTH_RETAILER_STALL_SEGMENTS:
            return _Candidate(
                entity_id=retailer_id, action_type=ACTION_ADJUST_PROMOTION_STRATEGY,
                params={"strategy_changes": {"promotion_strategy": "periodic", "promotion_discount": _PROMOTION_STRATEGY_DISCOUNT}},
                rationale=(
                    f"{retailer_id} is ml.clusters segment_name={segment_name} - switching to periodic "
                    f"promotions ({_PROMOTION_STRATEGY_DISCOUNT:.0%} discount) to stimulate stalled demand."
                ),
                confidence=0.5, priority=1.5,
            )

        return None

    def _apply_one(self, twin: DigitalTwinState, action: AgentAction) -> bool:
        """Operational actions (see `_STRATEGY_MUTATION_ACTIONS`) call
        `scenario_engine.advance_twin()` with this retailer's modified
        `RetailerStrategy` in effect for one real tick - see module
        docstring for why that's the genuine "apply" here, unlike every
        other agent module's direct `DigitalTwinState` field mutation.
        `recommend_long_term_change` falls through to the inherited
        `_apply_one()`, which recognizes neither `new_price` nor
        `inventory_delta` in its params and correctly leaves it PROPOSED -
        advisory, same as `pricing_agent.py`'s `freeze_price`."""
        if action.action_type not in _STRATEGY_MUTATION_ACTIONS:
            return super()._apply_one(twin, action)

        from simulation.scenario_engine import advance_twin

        try:
            strategy = RetailerStrategy(**action.params.get("strategy_changes", {}))
            advance_twin(
                twin, ticks=1, seed=int(action.params.get("seed", 42)),
                retailer_strategy_overrides={action.entity_id: strategy}, db_path=self.db_path,
            )
        except Exception as exc:  # noqa: BLE001 - a failed strategy application must not crash the run
            action.params["apply_error"] = repr(exc)
            return False
        return True


def _latest_retailer_segments(context: AgentContext) -> dict[str, str]:
    """One `segment_name` per retailer, from the most recent `ml.clusters`
    (Phase 6) computed_at batch - same dedup shape `demand_agent.py`'s
    `_latest_clusters()` uses, kept local here since this module only needs
    the segment name, not the full cluster row."""
    rows = [r for r in context.twin.ml_predictions.clusters if r.get("entity_type") == "retailer"]
    if not rows:
        return {}
    latest_computed_at = max(r.get("computed_at") or "" for r in rows)
    out: dict[str, str] = {}
    for row in rows:
        if (row.get("computed_at") or "") != latest_computed_at:
            continue
        entity_id = row.get("entity_id")
        if entity_id is not None and entity_id not in out:
            out[entity_id] = row.get("segment_name")
    return out
