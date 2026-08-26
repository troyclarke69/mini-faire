"""Autonomous Agent Orchestration (PHASE9-AUTONOMY.md Section 7).

Run directly: `python orchestration/agent_flow.py` (Section 10's entry point
- see `__main__` below, which demonstrates all three run modes).

Ties together every `autonomy/*.py` module from Sections 1-6 into one pass,
mirroring `orchestration/simulation_flow.py`'s shape (load -> run, isolated
per stage -> resolve/apply -> persist -> lineage -> `elt_model_runs`) with
one addition Phase 8's flow didn't need: conflict resolution between five
independently-deciding agents.

Three run modes (PHASE9-AUTONOMY.md Section 1's "must run continuously/
per-tick/per-scenario/per-tenant" - "continuously" is satisfied by an
external caller re-invoking this flow on a schedule, same as
`realtime_flow.py`'s own external-cron posture; "per-tenant" is satisfied by
the existing `tenant_id` param already threading through `load_digital_twin()`;
the two below are what this flow itself implements):

- `mode="live"` (default): one decide/resolve/apply round against the
  current live twin - the simplest, safest default for a generic caller
  (e.g. a future `api/autonomy_api.py` `/autonomy/run` endpoint).
- `mode="tick"`: `rounds` decide/resolve/apply rounds, with a real
  `scenario_engine.advance_twin()` call (organic marketplace activity, no
  agent involvement) between rounds - PHASE9-AUTONOMY.md Section 1's
  "per simulation tick (digital-twin mode)", exactly what `advance_twin()`'s
  own docstring names this flow as being built for: an agent's tick-N
  decision is still in effect - and can compound with organic ABM activity -
  when tick N+1's round runs, rather than each round starting over.
- `mode="scenario"`: agents decide against a scenario-mutated twin (built
  via `scenario_engine.build_scenario_twin()` - added in Task #85
  specifically "for handing agents a 'what the world looks like right after
  this scenario' twin", finally consumed by its real caller here) instead of
  the live one - "what would these agents do if this scenario happened".

Conflict resolution (`_resolve_and_apply()` below) is a FIXED, DOCUMENTED
priority order across the five agent_types, not a learned/negotiated
outcome (`agent_framework.py`'s module docstring's explicit Phase 10
boundary): `anomaly_response > inventory > pricing > retailer_strategy >
demand`. Proposals are processed in that order; the first proposal to claim
an entity (entity_type, entity_id) wins via `agent_framework.
BaseAutonomousAgent.enforce_constraints()`'s existing `cooldown_entities`
check (populated here, exactly as that method's own docstring says it
should be) - every later proposal for the same entity is rejected by that
same shared gate, so this flow doesn't need a second, separate rejection
mechanism. This ordering satisfies PHASE9-AUTONOMY.md Section 7's three
named conflict pairs exactly:

- "pricing vs demand" -> pricing wins (an inventory-risk-grounded pricing
  decision is more operationally urgent than an opportunistic demand-driven
  promotion for the same product).
- "inventory vs pricing" -> inventory wins (a physical-stock reality beats a
  forecast/risk-driven pricing call for the same product).
- "anomaly-response vs retailer strategy" -> anomaly_response wins (a
  specific, detected/evidenced problem outranks a general structural
  strategy adjustment for the same retailer).

The remaining two pairs (retailer_strategy vs demand, and any product-level
collision between anomaly_response and pricing/inventory/demand) aren't
individually named in the spec, so this flow applies the same total
ordering to them too rather than leaving them undefined - `retailer_strategy`
ranks above `demand` (an operational strategy change is more consequential
than a marketing recommendation) and `anomaly_response` ranks above every
other agent (see above).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from autonomy.agent_framework import (
    ACTION_STATUS_REJECTED,
    AgentAction,
    AgentContext,
    BaseAutonomousAgent,
    persist_actions,
)
from autonomy.anomaly_response_agent import AnomalyResponseAgent
from autonomy.demand_agent import DemandAgent
from autonomy.inventory_agent import InventoryAgent
from autonomy.pricing_agent import PricingAgent
from autonomy.retailer_strategy_agent import RetailerStrategyAgent
from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DUCKDB_PATH
from simulation.digital_twin import DigitalTwinState, load_digital_twin
from simulation.scenario_engine import DEFAULT_TICKS, advance_twin, build_scenario_twin, run_baseline_projection

# Fixed, documented priority order - see module docstring. Index 0 = highest
# priority (processed, and therefore able to claim a contested entity, first).
AGENT_TYPE_PRIORITY = ("anomaly_response", "inventory", "pricing", "retailer_strategy", "demand")

RUN_MODES = ("live", "tick", "scenario")


def _priority_rank(agent_type: str) -> int:
    try:
        return AGENT_TYPE_PRIORITY.index(agent_type)
    except ValueError:
        return len(AGENT_TYPE_PRIORITY)  # an unrecognized agent_type sorts last, not an error


def _build_agents(db_path: Path) -> dict[str, BaseAutonomousAgent]:
    return {
        "pricing": PricingAgent(),
        "inventory": InventoryAgent(),
        "demand": DemandAgent(),
        "anomaly_response": AnomalyResponseAgent(db_path=db_path),
        "retailer_strategy": RetailerStrategyAgent(db_path=db_path),
    }


def _dispatch_agent_failure(agent_type: str, exc: Exception) -> None:
    print(f"  agent_flow: autonomy_agent_failure for {agent_type!r}: {exc!r}")
    try:
        from alerts.dispatcher import dispatch_alert

        dispatch_alert(
            "autonomy_agent_failure",
            entity=f"agent_flow://{agent_type}",
            message=f"autonomy_agent_failure for {agent_type!r}: {exc!r}",
            severity="warning",
        )
    except Exception as dispatch_exc:  # noqa: BLE001 - alerting must never break the agent run
        print(f"  could not dispatch autonomy_agent_failure alert: {dispatch_exc!r}")


def _pipeline_health_check(db_path: Path, *, window_hours: float = 1.0) -> bool:
    """`AgentContext.pipeline_healthy` - the one signal Section 1's "agent
    constraints"/context needs that doesn't come from `DigitalTwinState`
    (see `agent_framework.AgentContext`'s docstring). Real, not a stub: a
    critical `monitoring.alert_events` row in the last `window_hours` means
    the pipeline is actively on fire, so agents can factor that into
    `decide()` if a future agent module wants to (none currently gate on it,
    but the signal is threaded through, real, and available). Degrades to
    `True` ("assume healthy") on any failure - a missing `monitoring` schema
    on a fresh warehouse isn't evidence of an unhealthy pipeline, matching
    this repo's "missing table == nothing recorded yet, not a crash"
    convention used throughout (`load_digital_twin()`, `list_scenario_
    results()`, this package's own `list_actions()`)."""
    try:
        with connect_with_retry(db_path, read_only=True) as con:
            # f-string interval interpolation matches monitoring/metrics.py's
            # own established pattern (DuckDB INTERVAL literals don't take a
            # `?` placeholder reliably) - window_hours is caller-controlled,
            # never external input, so this is safe.
            row = con.execute(
                f"select count(*) from monitoring.alert_events "
                f"where severity = 'critical' and created_at > current_timestamp - interval '{window_hours} hours'"
            ).fetchone()
        return not row or not row[0]
    except Exception:  # noqa: BLE001 - see docstring: degrade to "assume healthy"
        return True


def _collect_proposals(agents: dict[str, BaseAutonomousAgent], context: AgentContext) -> list[AgentAction]:
    """Each agent's `decide()` isolated in its own try/except - Section 7's
    "run each agent, isolated" bullet, same "one bad stage shouldn't take
    down the others" convention `simulation_flow.py`/`ml_inference_flow.py`
    already use for their own per-stage loops."""
    proposals: list[AgentAction] = []
    for agent_type, agent in agents.items():
        try:
            proposals.extend(agent.decide(context))
        except Exception as exc:  # noqa: BLE001 - one failing agent shouldn't block the other four
            _dispatch_agent_failure(agent_type, exc)
    return proposals


def _resolve_and_apply(
    agents: dict[str, BaseAutonomousAgent], proposals: list[AgentAction], twin: DigitalTwinState, context: AgentContext
) -> tuple[list[AgentAction], list[AgentAction], list[dict[str, Any]]]:
    """Returns (resolved_and_applied, rejected, conflict_records) - see
    module docstring for the priority order and how `cooldown_entities`
    does the actual arbitration."""
    ordered = sorted(proposals, key=lambda a: (_priority_rank(a.agent_type), -a.confidence))

    survivors: list[AgentAction] = []
    rejected: list[AgentAction] = []
    winner_by_entity: dict[tuple[str, str], AgentAction] = {}
    conflicts: list[dict[str, Any]] = []

    for action in ordered:
        agent = agents.get(action.agent_type)
        if agent is None:
            # An action from an agent_type this flow doesn't know how to
            # route (shouldn't happen given _build_agents() above, but not
            # worth crashing the run over) - reject rather than silently drop.
            action.status = ACTION_STATUS_REJECTED
            rejected.append(action)
            continue

        entity_key = (action.entity_type, action.entity_id)
        already_claimed = entity_key in context.cooldown_entities
        checked = agent.enforce_constraints(action, context)
        if checked is None:
            action.status = ACTION_STATUS_REJECTED
            rejected.append(action)
            if already_claimed:
                winner = winner_by_entity.get(entity_key)
                conflicts.append({
                    "conflict_id": f"conflict_{uuid.uuid4().hex[:12]}",
                    "run_id": context.run_id,
                    "entity_type": action.entity_type, "entity_id": action.entity_id,
                    "winning_agent_type": winner.agent_type if winner else None,
                    "winning_action_type": winner.action_type if winner else None,
                    "winning_action_id": winner.action_id if winner else None,
                    "rejected_agent_type": action.agent_type, "rejected_action_type": action.action_type,
                    "rejected_action_id": action.action_id,
                    "created_at": utc_now(),
                })
            continue
        context.cooldown_entities.add(entity_key)
        winner_by_entity.setdefault(entity_key, checked)
        survivors.append(checked)

    by_agent_type: dict[str, list[AgentAction]] = {}
    for action in survivors:
        by_agent_type.setdefault(action.agent_type, []).append(action)

    applied: list[AgentAction] = []
    for agent_type, actions in by_agent_type.items():
        applied.extend(agents[agent_type].act(twin, actions))

    return applied, rejected, conflicts


def _append_agent_run(
    db_path: Path, *, agent_type: str, target_table: str, row_count: int, started_at: str, completed_at: str, status: str
) -> None:
    """Same `elt_model_runs` table every other orchestration flow writes to
    - see `simulation_flow.py`'s `_append_simulation_run()`/`ml_inference_
    flow.py`'s `_append_inference_run()` for the identical shape."""
    with connect_with_retry(db_path) as con:
        con.execute(
            """
            create table if not exists elt_model_runs (
              model_name varchar, target_table varchar, load_strategy varchar, business_key varchar,
              source_row_count integer, affected_key_count integer, target_row_count integer,
              high_watermark timestamptz, started_at timestamptz, completed_at timestamptz, status varchar
            )
            """
        )
        con.execute(
            "insert into elt_model_runs values (?, ?, 'autonomy_agent', 'n/a', ?, ?, ?, null, ?, ?, ?)",
            [agent_type, target_table, row_count, row_count, row_count, started_at, completed_at, status],
        )


def _persist_round(agents: dict[str, BaseAutonomousAgent], actions: list[AgentAction], db_path: Path, run_id: str) -> None:
    """Persists every resolved action (applied or rejected - the rejected
    ones are as much a real audit-trail row as the applied ones, see
    `AgentAction.status`) to its agent_type's own `autonomy.<table>`, plus
    one lineage edge per agent_type per round and one `elt_model_runs` row
    per agent_type - mirrors `simulation_flow.py`'s per-scenario `elt_model_
    runs` append, at agent-type granularity instead of per-action, matching
    `ml_inference_flow.py`'s own per-model-type (not per-row) granularity."""
    by_agent_type: dict[str, list[AgentAction]] = {}
    for action in actions:
        by_agent_type.setdefault(action.agent_type, []).append(action)

    for agent_type, agent_actions in by_agent_type.items():
        agent = agents.get(agent_type)
        table_name = getattr(agent, "table_name", f"{agent_type}_actions")
        started_at = utc_now()
        status = "failed"
        try:
            persist_actions(agent_actions, table_name, db_path)
            status = "success"
        except Exception as exc:  # noqa: BLE001 - a persistence failure for one agent must not block the others
            print(f"  agent_flow: could not persist {len(agent_actions)} {agent_type} action(s): {exc!r}")

        try:
            upsert_lineage_edges(
                [
                    LineageEdge(
                        run_id=run_id,
                        source_node="simulation.digital_twin,ml.forecasts,ml.clusters,ml.recommendations,anomalies.anomaly_events",
                        target_node=f"autonomy.{table_name}",
                        edge_type="autonomy_agent_decided",
                        entity=agent_type,
                        created_at=utc_now(),
                    )
                ],
                db_path,
            )
        except Exception as exc:  # noqa: BLE001 - lineage is best-effort, never worth crashing the run over
            print(f"  agent_flow: could not emit lineage edge for {agent_type}: {exc!r}")

        try:
            _append_agent_run(
                db_path, agent_type=agent_type, target_table=f"autonomy.{table_name}",
                row_count=len(agent_actions), started_at=started_at, completed_at=utc_now(), status=status,
            )
        except Exception as append_exc:  # noqa: BLE001 - recording the run must never itself crash the flow
            print(f"  agent_flow: could not record {agent_type} agent run: {append_exc!r}")


def _persist_conflicts(conflicts: list[dict[str, Any]], run_id: str, db_path: Path) -> None:
    """`autonomy.conflicts` - one row per entity-collision this run resolved
    (see `_resolve_and_apply()` above for how `conflict_id`/`created_at` get
    set). Added so `api/autonomy_api.py`'s conflicts endpoint/stream (Section
    8: "WS/SSE push for new decisions/conflicts/resolutions/performance")
    has a real, queryable source instead of re-deriving "what lost to what"
    from the five action tables at request time - the conflict record is
    already fully structured right here, at the moment resolution actually
    happens, so persisting it directly is simpler and cheaper than
    reconstructing it later from `status='rejected'` rows joined back
    against their winners. A no-op on an empty list, matching `agent_
    framework.persist_actions()`'s same convention."""
    if not conflicts:
        return
    try:
        with connect_with_retry(db_path) as con:
            con.execute(
                """
                create table if not exists autonomy.conflicts (
                  conflict_id varchar primary key, run_id varchar, entity_type varchar, entity_id varchar,
                  winning_agent_type varchar, winning_action_type varchar, winning_action_id varchar,
                  rejected_agent_type varchar, rejected_action_type varchar, rejected_action_id varchar,
                  created_at timestamptz
                )
                """
            )
            con.executemany(
                "insert into autonomy.conflicts values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        c["conflict_id"], c["run_id"], c["entity_type"], c["entity_id"],
                        c["winning_agent_type"], c["winning_action_type"], c["winning_action_id"],
                        c["rejected_agent_type"], c["rejected_action_type"], c["rejected_action_id"],
                        c["created_at"],
                    )
                    for c in conflicts
                ],
            )
    except Exception as exc:  # noqa: BLE001 - persisting conflicts is best-effort, never worth crashing the run over
        print(f"  agent_flow: could not persist {len(conflicts)} conflict(s): {exc!r}")

    try:
        upsert_lineage_edges(
            [
                LineageEdge(
                    run_id=run_id, source_node="autonomy.pricing_actions,autonomy.inventory_actions,"
                    "autonomy.demand_actions,autonomy.anomaly_actions,autonomy.retailer_strategy_actions",
                    target_node="autonomy.conflicts", edge_type="autonomy_conflict_resolved",
                    entity="agent_flow", created_at=utc_now(),
                )
            ],
            db_path,
        )
    except Exception as exc:  # noqa: BLE001 - lineage is best-effort, same posture as _persist_round()
        print(f"  agent_flow: could not emit lineage edge for conflicts: {exc!r}")


def run_agent_flow(
    *,
    mode: str = "live",
    tenant_id: str | None = None,
    scenario_type: str | None = None,
    scenario_params: dict[str, Any] | None = None,
    rounds: int = 1,
    ticks_per_round: int = 1,
    projection_ticks: int = DEFAULT_TICKS,
    seed: int = 42,
    db_path: Path = DUCKDB_PATH,
) -> dict[str, Any]:
    """Runs one full autonomous-agent pass end to end - see module docstring
    for the three run modes and the conflict-resolution priority order.
    Returns a summary dict (mirroring `simulation_flow.py`'s own summary
    shape) with per-agent action counts, the resolved conflicts, and the
    run-level reward (see `agent_framework.BaseAutonomousAgent.score_reward()`'s
    docstring - one baseline-projection GMV delta for the whole run,
    attributed identically to every non-rejected action from it)."""
    if mode not in RUN_MODES:
        raise ValueError(f"unknown mode {mode!r} - must be one of {RUN_MODES}")
    if not db_path.exists():
        print("  agent_flow: warehouse not built yet - run scripts/run_demo.py first")
        return {}

    run_id = f"agent_flow_{uuid.uuid4().hex[:12]}"
    t0 = time.monotonic()

    print(f"  agent_flow: loading twin (mode={mode})...")
    if mode == "scenario":
        if not scenario_type:
            raise ValueError("mode='scenario' requires scenario_type")
        twin = build_scenario_twin(scenario_type, scenario_params or {}, tenant_id=tenant_id, db_path=db_path)
    else:
        twin = load_digital_twin(tenant_id, db_path)
    print(
        f"  agent_flow: twin loaded - {len(twin.retailers)} retailer(s), {len(twin.products)} product(s), "
        f"{len(twin.recent_anomalies)} anomaly record(s)"
    )

    pipeline_healthy = _pipeline_health_check(db_path)
    agents = _build_agents(db_path)

    total_rounds = rounds if mode == "tick" else 1
    gmv_before = run_baseline_projection(twin, ticks=projection_ticks, seed=seed, db_path=db_path)["gmv"]

    all_applied: list[AgentAction] = []
    all_rejected: list[AgentAction] = []
    all_conflicts: list[dict[str, Any]] = []

    for round_num in range(max(1, total_rounds)):
        context = AgentContext(twin=twin, tenant_id=tenant_id, run_id=run_id, seed=seed, pipeline_healthy=pipeline_healthy)
        proposals = _collect_proposals(agents, context)
        applied, rejected, conflicts = _resolve_and_apply(agents, proposals, twin, context)
        print(
            f"  agent_flow: round {round_num + 1}/{total_rounds} - {len(proposals)} proposed, "
            f"{len(applied)} applied/advisory, {len(rejected)} rejected, {len(conflicts)} conflict(s)"
        )
        all_applied.extend(applied)
        all_rejected.extend(rejected)
        all_conflicts.extend(conflicts)

        if mode == "tick" and round_num < total_rounds - 1:
            advance_twin(twin, ticks=ticks_per_round, seed=seed, db_path=db_path)

    gmv_after = run_baseline_projection(twin, ticks=projection_ticks, seed=seed, db_path=db_path)["gmv"]
    reward = round(gmv_after - gmv_before, 2)

    for action in all_applied:
        agent = agents.get(action.agent_type)
        if agent is not None:
            action.reward = agent.score_reward(action, gmv_before, gmv_after)

    _persist_round(agents, all_applied + all_rejected, db_path, run_id)
    _persist_conflicts(all_conflicts, run_id, db_path)

    summary = {
        "run_id": run_id,
        "mode": mode,
        "rounds": total_rounds,
        "pipeline_healthy": pipeline_healthy,
        "proposed_count": len(all_applied) + len(all_rejected),
        "applied_count": sum(1 for a in all_applied if a.status == "applied"),
        "advisory_count": sum(1 for a in all_applied if a.status == "proposed"),
        "rejected_count": len(all_rejected),
        "conflicts": all_conflicts,
        "gmv_before": gmv_before,
        "gmv_after": gmv_after,
        "reward": reward,
        "action_counts_by_agent": {
            agent_type: sum(1 for a in all_applied + all_rejected if a.agent_type == agent_type)
            for agent_type in agents
        },
        "elapsed_seconds": round(time.monotonic() - t0, 2),
    }
    return summary


if __name__ == "__main__":
    print("== agent_flow: live mode ==")
    live_summary = run_agent_flow(mode="live")
    print(f"Agent flow (live) complete: {live_summary}")

    print("\n== agent_flow: tick mode (2 rounds) ==")
    tick_summary = run_agent_flow(mode="tick", rounds=2, ticks_per_round=2)
    print(f"Agent flow (tick) complete: {tick_summary}")

    print("\n== agent_flow: scenario mode (demand_shock) ==")
    scenario_summary = run_agent_flow(mode="scenario", scenario_type="demand_shock", scenario_params={"multiplier": 1.5})
    print(f"Agent flow (scenario) complete: {scenario_summary}")
