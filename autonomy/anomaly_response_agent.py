"""Anomaly-Response Agent (PHASE9-AUTONOMY.md Section 5).

One `AnomalyResponseAgent` decides across every anomaly on `context.twin.
recent_anomalies` (`anomalies.anomaly_events`, Phase 5's real detector -
`anomalies/detector.py`) in a single `decide()` call. Every one of the eight
real `anomaly_type` values that detector module actually produces gets a
distinct, documented response - no anomaly type is silently ignored:

- `price_anomaly` (entity_type="product") splits on DIRECTION, a genuine
  business distinction rather than an arbitrary split: `metric_value >
  baseline_value` (price crept up) -> `adjust_pricing`, a *dampened*
  corrective nudge (half the deviation, not a full revert - contrast with
  `pricing_agent.py`'s `revert_price`, which trusts the anomaly detector's
  baseline completely; this agent is more conservative since it's reacting
  to the anomaly signal alone, not also cross-checking reorder risk/forecasts
  the way `pricing_agent.py` does). `metric_value < baseline_value` (price
  dropped anomalously) -> `adjust_promotions` instead: leaning into an
  unplanned low price as a bounded, time-limited promotion rather than
  immediately erasing it, since an anomalously low price isn't always a
  mistake worth reverting the instant it's noticed.
- `inventory_stockout` (entity_type="product") -> `adjust_inventory`, a
  smaller "emergency" restock than `inventory_agent.py`'s own
  `reorder_inventory` (which reacts to the *routine* `reorder_risk_band`
  heuristic) - this one exists specifically for a stockout the anomaly
  detector caught that the risk score hasn't (yet) reflected.
- `retailer_health_degradation` (entity_type="retailer") ->
  `trigger_simulation_scenario` with `scenario_type="retailer_outage"` -
  projects the downside if this retailer's degrading health continues to a
  full outage, a genuinely unique lever only this agent has (see
  `_apply_one()` below - it's the one action_type across every autonomy
  agent module that calls into `simulation/scenario_engine.py` for a real
  side effect instead of a twin mutation).
- `gmv_spike`/`gmv_drop` (entity_type="system") ->
  `trigger_counterfactual_analysis` with `counterfactual_type=
  "remove_anomaly_window"`, `params={"anomaly_id": ...}` - a direct,
  parameter-for-parameter fit (`simulation/counterfactuals.py`'s
  `COUNTERFACTUAL_REMOVE_ANOMALY_WINDOW` takes exactly an `anomaly_id`),
  investigating what this specific detected window actually cost/gained.
- `order_velocity_change`/`event_lag_spike`/`ingestion_volume_anomaly`/
  `quarantine_rate_anomaly` -> `notify_tenant_admins`. All four are
  marketplace-wide or pipeline/data-quality signals (see each anomaly_type's
  `entity_type` in `anomalies/detector.py` - "system"/"event_type"/"entity",
  never a single product or retailer this agent could act on directly), so
  there's no twin-mutation lever to pull; logging the decision for a human
  to look at is the honest response, not inventing one.

`score_reward()` is overridden for the two `trigger_*` action types only -
see its docstring. Every other action type keeps `BaseAutonomousAgent`'s
default run-level GMV-delta approximation, same as every other agent module.
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
from simulation.digital_twin import AnomalyRecord, DigitalTwinState

ACTION_ADJUST_PRICING = "adjust_pricing"
ACTION_ADJUST_INVENTORY = "adjust_inventory"
ACTION_ADJUST_PROMOTIONS = "adjust_promotions"
ACTION_NOTIFY_TENANT_ADMINS = "notify_tenant_admins"
ACTION_TRIGGER_SIMULATION_SCENARIO = "trigger_simulation_scenario"
ACTION_TRIGGER_COUNTERFACTUAL_ANALYSIS = "trigger_counterfactual_analysis"

ACTION_TYPES = (
    ACTION_ADJUST_PRICING, ACTION_ADJUST_INVENTORY, ACTION_ADJUST_PROMOTIONS, ACTION_NOTIFY_TENANT_ADMINS,
    ACTION_TRIGGER_SIMULATION_SCENARIO, ACTION_TRIGGER_COUNTERFACTUAL_ANALYSIS,
)

# adjust_pricing corrects only half the detected deviation - deliberately
# more conservative than pricing_agent.py's revert_price (a full revert to
# baseline_value) since this agent is reacting to the anomaly signal alone.
_PRICE_CORRECTION_FRACTION = 0.5
_MARGIN_FLOOR_PCT = 0.05  # same guardrail shape as pricing_agent.py/demand_agent.py, duplicated for the same reason
ACTION_ADJUST_PROMOTIONS_DISCOUNT = 0.15

# System/pipeline anomaly types with no per-entity twin lever - see module
# docstring's "notify_tenant_admins" bullet.
_NOTIFY_ONLY_ANOMALY_TYPES = {
    "order_velocity_change", "event_lag_spike", "ingestion_volume_anomaly", "quarantine_rate_anomaly",
}

_SEVERITY_CONFIDENCE = {"critical": 0.9, "warning": 0.6}
_DEFAULT_CONFIDENCE = 0.5


@dataclass
class _Candidate:
    entity_type: str
    entity_id: str
    action_type: str
    params: dict[str, Any]
    rationale: str
    confidence: float
    priority: float


class AnomalyResponseAgent(BaseAutonomousAgent):
    agent_type = "anomaly_response"
    table_name = "anomaly_actions"  # autonomy.anomaly_actions

    def __init__(self, *args: Any, db_path: Path = DUCKDB_PATH, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # Only this agent's _apply_one() needs a warehouse connection (to
        # actually run the triggered scenario/counterfactual) - no other
        # agent module touches DuckDB directly, they all read purely from
        # context.twin/ml_predictions, so this is agent-specific state, not
        # something agent_framework.BaseAutonomousAgent needs to carry.
        self.db_path = db_path

    def decide(self, context: AgentContext) -> list[AgentAction]:
        self.observe(context)
        self.state = AGENT_STATE_DECIDING

        candidates: list[_Candidate] = []
        for anomaly in context.twin.recent_anomalies:
            candidate = self._decide_one(anomaly, context)
            if candidate is not None:
                candidates.append(candidate)

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

    def _decide_one(self, anomaly: AnomalyRecord, context: AgentContext) -> _Candidate | None:
        confidence = _SEVERITY_CONFIDENCE.get(anomaly.severity, _DEFAULT_CONFIDENCE)
        priority_base = {"critical": 3.0, "warning": 2.0}.get(anomaly.severity, 1.0)

        if anomaly.anomaly_type == "price_anomaly":
            return self._price_anomaly_candidate(anomaly, context, confidence, priority_base)

        if anomaly.anomaly_type == "inventory_stockout":
            product = context.twin.products.get(anomaly.entity_id)
            quantity = max(5, int(round((product.units_sold if product else 0) * 0.5)) or 5)
            return _Candidate(
                entity_type="product", entity_id=anomaly.entity_id, action_type=ACTION_ADJUST_INVENTORY,
                params={
                    "inventory_delta": quantity, "quantity": quantity, "base_reorder_quantity": quantity,
                    "anomaly_id": anomaly.anomaly_id, "metric_value": anomaly.metric_value,
                },
                rationale=(
                    f"anomalies.anomaly_events flagged inventory_stockout on {anomaly.entity_id} "
                    f"(severity={anomaly.severity}) - an emergency restock of {quantity} units ahead of "
                    f"inventory_agent.py's next routine pass."
                ),
                confidence=confidence, priority=priority_base + 0.5,
            )

        if anomaly.anomaly_type == "retailer_health_degradation":
            return _Candidate(
                entity_type="retailer", entity_id=anomaly.entity_id, action_type=ACTION_TRIGGER_SIMULATION_SCENARIO,
                params={
                    "scenario_type": "retailer_outage", "scenario_params": {"retailer_id": anomaly.entity_id},
                    "anomaly_id": anomaly.anomaly_id,
                },
                rationale=(
                    f"anomalies.anomaly_events flagged retailer_health_degradation on {anomaly.entity_id} "
                    f"(health={anomaly.metric_value}, baseline={anomaly.baseline_value}) - projecting the "
                    f"downside via a retailer_outage scenario in case this becomes a full outage."
                ),
                confidence=confidence, priority=priority_base + 0.4,
            )

        if anomaly.anomaly_type in ("gmv_spike", "gmv_drop"):
            return _Candidate(
                entity_type=anomaly.entity_type, entity_id=anomaly.entity_id,
                action_type=ACTION_TRIGGER_COUNTERFACTUAL_ANALYSIS,
                params={
                    "counterfactual_type": "remove_anomaly_window",
                    "counterfactual_params": {"anomaly_id": anomaly.anomaly_id}, "anomaly_id": anomaly.anomaly_id,
                },
                rationale=(
                    f"anomalies.anomaly_events flagged {anomaly.anomaly_type} (deviation={anomaly.deviation}) - "
                    f"investigating this specific window's real impact via a remove_anomaly_window counterfactual."
                ),
                confidence=confidence, priority=priority_base + 0.3,
            )

        if anomaly.anomaly_type in _NOTIFY_ONLY_ANOMALY_TYPES:
            return _Candidate(
                entity_type=anomaly.entity_type, entity_id=anomaly.entity_id, action_type=ACTION_NOTIFY_TENANT_ADMINS,
                params={
                    "anomaly_id": anomaly.anomaly_id, "anomaly_type": anomaly.anomaly_type,
                    "metric_value": anomaly.metric_value, "baseline_value": anomaly.baseline_value,
                },
                rationale=(
                    f"anomalies.anomaly_events flagged {anomaly.anomaly_type} on {anomaly.entity_type}="
                    f"{anomaly.entity_id} (severity={anomaly.severity}) - a marketplace/pipeline-level signal "
                    f"with no single-entity twin lever, so this is logged for a human to review rather than acted on."
                ),
                confidence=confidence, priority=priority_base,
            )

        # An anomaly_type this agent doesn't recognize (a future detector
        # addition) - no action, not a crash; same "unknown is a no-op, not
        # an error" posture as BaseAutonomousAgent._apply_one()'s own
        # unrecognized-action_type fallback.
        return None

    def _price_anomaly_candidate(
        self, anomaly: AnomalyRecord, context: AgentContext, confidence: float, priority_base: float
    ) -> _Candidate | None:
        product = context.twin.products.get(anomaly.entity_id)
        if product is None or product.unit_price is None or anomaly.baseline_value is None:
            return None
        current_price = product.unit_price
        baseline = float(anomaly.baseline_value)

        if current_price > baseline:
            # Priced anomalously HIGH - correct it back down, but only
            # halfway (see module docstring's "dampened" note).
            new_price = round(current_price - (current_price - baseline) * _PRICE_CORRECTION_FRACTION, 2)
            floor = 1.0
            if product.unit_cost is not None:
                floor = max(floor, round(product.unit_cost * (1.0 + _MARGIN_FLOOR_PCT), 2))
            new_price = max(new_price, floor)
            if abs(new_price - current_price) < 0.01:
                return None
            return _Candidate(
                entity_type="product", entity_id=anomaly.entity_id, action_type=ACTION_ADJUST_PRICING,
                params={
                    "current_price": current_price, "new_price": new_price, "anomaly_id": anomaly.anomaly_id,
                    "baseline_value": baseline, "correction_fraction": _PRICE_CORRECTION_FRACTION,
                },
                rationale=(
                    f"anomalies.anomaly_events flagged price_anomaly on {anomaly.entity_id} "
                    f"(current={current_price}, baseline={baseline}) - price is anomalously HIGH, correcting "
                    f"{_PRICE_CORRECTION_FRACTION:.0%} of the way back toward baseline."
                ),
                confidence=confidence, priority=priority_base + 0.5,
            )

        # Priced anomalously LOW - lean into it as a bounded promotion
        # rather than immediately erasing it (see module docstring).
        new_price = round(current_price * (1.0 - ACTION_ADJUST_PROMOTIONS_DISCOUNT), 2)
        if new_price >= current_price or new_price < 1.0:
            new_price = current_price
        return _Candidate(
            entity_type="product", entity_id=anomaly.entity_id, action_type=ACTION_ADJUST_PROMOTIONS,
            params={
                "current_price": current_price, "new_price": new_price, "discount": ACTION_ADJUST_PROMOTIONS_DISCOUNT,
                "anomaly_id": anomaly.anomaly_id, "baseline_value": baseline,
            },
            rationale=(
                f"anomalies.anomaly_events flagged price_anomaly on {anomaly.entity_id} "
                f"(current={current_price}, baseline={baseline}) - price is anomalously LOW; treating it as a "
                f"bounded {ACTION_ADJUST_PROMOTIONS_DISCOUNT:.0%} promotion rather than reverting immediately."
            ),
            confidence=confidence, priority=priority_base + 0.4,
        )

    def score_reward(self, action: AgentAction, gmv_before: float, gmv_after: float) -> float:
        """`trigger_simulation_scenario`/`trigger_counterfactual_analysis`
        have a real, EXACT, action-specific reward available once applied -
        `_apply_one()` below stashes the triggered run's own
        `predicted_gmv_delta`/`counterfactual_gmv_delta` onto
        `action.params["result_gmv_delta"]`. Every other action type falls
        back to the base class's run-level approximation, same as every
        other agent module."""
        if action.action_type in (ACTION_TRIGGER_SIMULATION_SCENARIO, ACTION_TRIGGER_COUNTERFACTUAL_ANALYSIS):
            exact = action.params.get("result_gmv_delta")
            if exact is not None:
                return round(float(exact), 2)
        return super().score_reward(action, gmv_before, gmv_after)

    def _apply_one(self, twin: DigitalTwinState, action: AgentAction) -> bool:
        """`trigger_simulation_scenario`/`trigger_counterfactual_analysis`
        are this agent's one genuinely unique lever - a real call into
        `simulation/scenario_engine.py`/`simulation/counterfactuals.py`
        rather than a twin mutation (neither call mutates `twin` itself -
        both clone internally, see their own docstrings - so this is safe to
        do here in `act()`, after conflict resolution has had its say, same
        as every other `_apply_one()` override in this package). Any
        adjust_pricing/adjust_inventory/adjust_promotions action falls
        through to the inherited new_price/inventory_delta handling -
        exactly the same mechanism pricing_agent.py/demand_agent.py/
        inventory_agent.py already use, no override needed for those."""
        if action.action_type == ACTION_TRIGGER_SIMULATION_SCENARIO:
            return self._trigger_scenario(action)
        if action.action_type == ACTION_TRIGGER_COUNTERFACTUAL_ANALYSIS:
            return self._trigger_counterfactual(action)
        return super()._apply_one(twin, action)

    def _trigger_scenario(self, action: AgentAction) -> bool:
        from simulation.scenario_engine import run_scenario

        try:
            result = run_scenario(
                action.params["scenario_type"], dict(action.params.get("scenario_params") or {}),
                db_path=self.db_path,
            )
        except Exception as exc:  # noqa: BLE001 - a triggered scenario failing must not crash the run
            action.params["trigger_error"] = repr(exc)
            return False
        action.params["scenario_id"] = result.scenario_id
        action.params["result_gmv_delta"] = result.predicted_gmv_delta
        return True

    def _trigger_counterfactual(self, action: AgentAction) -> bool:
        from simulation.counterfactuals import run_counterfactual

        try:
            result = run_counterfactual(
                action.params["counterfactual_type"], dict(action.params.get("counterfactual_params") or {}),
                db_path=self.db_path,
            )
        except Exception as exc:  # noqa: BLE001 - a triggered counterfactual failing must not crash the run
            action.params["trigger_error"] = repr(exc)
            return False
        action.params["counterfactual_id"] = result.counterfactual_id
        action.params["result_gmv_delta"] = result.counterfactual_gmv_delta
        return True
