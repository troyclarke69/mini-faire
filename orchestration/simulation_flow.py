"""Simulation Orchestration (PHASE8-SIMULATION.md Section 5).

Run directly: `python orchestration/simulation_flow.py` (Section 8: "Everything
must run via `python orchestration/simulation_flow.py`" - see `__main__`
below for what that no-argument run actually does).

Ties together every other Phase 8 module into one pass, in the order the
spec lists:

- "load digital twin" -> `simulation.digital_twin.load_digital_twin()`.
- "load agents" -> `simulation.scenario_engine.build_agents()`, called once
  here purely to log what got loaded (retailer/product agent counts) - each
  individual scenario/counterfactual run below builds its *own* fresh agent
  set internally (`run_scenario()`/`run_counterfactual()` each call
  `build_agents()` per branch), because `MarketplaceAgent` carries mutable
  per-run state (its own `random.Random`, decaying demand multiplier,
  category-trend walk) that must start clean for every seeded run to stay
  reproducible - reusing one instance across multiple runs would leak state
  between them and break that reproducibility.
- "load ML models" -> already satisfied by `load_digital_twin()` above,
  which reads the currently-active `ml.forecasts`/`clusters`/
  `recommendations`/`anomaly_classifications` the same way `retailer_agent.
  py`'s ML-driven pricing strategy reads them (see `digital_twin.py`'s
  module docstring). This flow does not retrain or refit anything itself -
  that stays `orchestration/ml_training_flow.py`'s job; this flow only
  consumes whatever `ml_inference_flow.py`'s last pass already produced.
- "run scenario engine" -> `_run_scenarios()`, one isolated call per spec to
  `scenario_engine.run_scenario()`.
- "run counterfactual engine" -> `_run_counterfactuals()`, one isolated call
  per spec to `counterfactuals.run_counterfactual()`.
- "run simulation ticks" -> `scenario_engine.run_baseline_projection()`, a
  plain seeded forward run with no scenario mutation applied - "where do
  things end up if nothing changes". This is the one bullet with no table of
  its own: a bare tick-advance isn't a distinct persistable artifact from
  "a demand_shock scenario with multiplier=1.0" (see that function's
  docstring), so its result is returned in this flow's summary dict rather
  than written to a new schema.
- "write results" / "emit lineage edges" -> already handled inside
  `scenario_engine.persist_scenario_result()`/`counterfactuals.
  persist_counterfactual_result()`, which this flow calls via `persist=True`.
- "append simulation runs to elt_model_runs" -> `_append_simulation_run()`
  below, one row per scenario/counterfactual attempt (success or failure),
  mirroring `orchestration/ml_inference_flow.py`'s own `_append_inference_
  run()` for the exact same table.

Each scenario/counterfactual run is isolated in its own try/except - this
repo's established "one bad stage shouldn't take down the others"
convention (`orchestration/realtime_flow.py`'s `_run_monitoring_pass()`,
`ml_training_flow.py`'s per-model-type loop, `ml_inference_flow.py`'s
`STAGES` loop) applies here too: a malformed scenario spec shouldn't stop
the rest of the batch, and dispatches a `simulation_scenario_failure` /
`simulation_counterfactual_failure` alert instead (mirroring `ml_inference_
flow.py`'s `ml_inference_failure`).

Unlike `ml_inference_flow.py`, there is no `config/simulation.yaml` gating
this flow on/off - PHASE8-SIMULATION.md doesn't ask for one, and this flow
is meant to be triggered explicitly (a CLI run, or later `api/simulation_
api.py`'s `/simulation/run` endpoint - Section 6), not run automatically on
every real-time refresh cycle the way anomaly detection/monitoring are.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import utc_now
from ingestion.paths import DUCKDB_PATH
from simulation import counterfactuals, scenario_engine
from simulation.digital_twin import DigitalTwinState, load_digital_twin


def _dispatch_simulation_failure(alert_type: str, identifier: str, exc: Exception) -> None:
    print(f"  simulation_flow: {alert_type} for {identifier!r}: {exc!r}")
    try:
        from alerts.dispatcher import dispatch_alert

        dispatch_alert(
            alert_type,
            entity=f"simulation_flow://{identifier}",
            message=f"{alert_type} for {identifier!r}: {exc!r}",
            severity="warning",
        )
    except Exception as dispatch_exc:  # noqa: BLE001 - alerting must never break the simulation run
        print(f"  could not dispatch {alert_type} alert: {dispatch_exc!r}")


def _append_simulation_run(
    db_path: Path, *, kind: str, identifier: str, started_at: str, completed_at: str, status: str
) -> None:
    """Same `elt_model_runs` table every other orchestration flow already
    writes to (`ml_inference_flow.py`'s `_append_inference_run()`,
    `fact_orders.sql`'s own insert, etc.) - `load_strategy` distinguishes a
    simulation run ('simulation_scenario' / 'simulation_counterfactual')
    from the ELT/ML rows already living there, and `model_name` carries the
    specific scenario_type/counterfactual_type so a query against this table
    can answer "how often has a retailer_outage scenario been run" without
    joining back into `simulation.scenario_results`."""
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
        target_table = "simulation.scenario_results" if kind == "simulation_scenario" else "simulation.counterfactual_results"
        con.execute(
            "insert into elt_model_runs values (?, ?, ?, 'n/a', 1, 1, 1, null, ?, ?, ?)",
            [identifier, target_table, kind, started_at, completed_at, status],
        )


def _default_scenario_specs(twin: DigitalTwinState) -> list[dict[str, Any]]:
    """Builds a small, illustrative demo batch entirely from real IDs
    already present in `twin`, rather than hardcoding synthetic-data IDs
    that would silently no-op (or raise `ScenarioError`) against a
    differently-seeded warehouse - Section 8 requires the no-argument
    `python orchestration/simulation_flow.py` entrypoint to produce a real,
    non-trivial run against whatever data actually exists, not against
    assumed fixture IDs. Not exhaustive over every `SCENARIO_TYPES` entry by
    design (e.g. `product_launch` invents a brand-new product_id, which
    isn't something to do unprompted on every default run) - a caller that
    wants a specific scenario type/params passes its own `scenario_specs`
    to `run_simulation_flow()` instead of relying on these defaults."""
    specs: list[dict[str, Any]] = [
        {"scenario_type": scenario_engine.SCENARIO_DEMAND_SHOCK, "params": {"multiplier": 1.6}},
        {"scenario_type": scenario_engine.SCENARIO_COMPETITOR_ENTRY, "params": {"pressure_increase": 0.2}},
    ]

    first_product_id = next(iter(twin.products), None)
    if first_product_id is not None:
        product = twin.products[first_product_id]
        specs.append(
            {"scenario_type": scenario_engine.SCENARIO_INVENTORY_CHANGE, "params": {"product_id": first_product_id, "delta": 50}}
        )
        if product.unit_price:
            specs.append(
                {
                    "scenario_type": scenario_engine.SCENARIO_PRICE_CHANGE,
                    "params": {"product_id": first_product_id, "new_price": round(product.unit_price * 1.15, 2)},
                }
            )

    first_retailer_id = next(iter(twin.retailers), None)
    if first_retailer_id is not None:
        specs.append(
            {
                "scenario_type": scenario_engine.SCENARIO_RETAILER_OUTAGE,
                "params": {"retailer_id": first_retailer_id, "duration_ticks": 3},
            }
        )
        specs.append(
            {
                "scenario_type": scenario_engine.SCENARIO_PROMOTION_EVENT,
                "params": {"retailer_id": first_retailer_id, "discount": 0.2, "duration_ticks": 3},
            }
        )

    return specs


def _default_counterfactual_specs(twin: DigitalTwinState) -> list[dict[str, Any]]:
    """Same "derive from real state, don't hardcode fixture IDs" posture as
    `_default_scenario_specs()` above, for the counterfactual batch."""
    specs: list[dict[str, Any]] = []

    first_retailer_id = next(iter(twin.retailers), None)
    if first_retailer_id is not None:
        specs.append(
            {"counterfactual_type": counterfactuals.COUNTERFACTUAL_REMOVE_RETAILER_ORDERS, "params": {"retailer_id": first_retailer_id}}
        )

    first_product_id = next(iter(twin.products), None)
    if first_product_id is not None:
        specs.append(
            {
                "counterfactual_type": counterfactuals.COUNTERFACTUAL_MODIFY_PRICE,
                "params": {"product_id": first_product_id, "price_multiplier": 0.9},
            }
        )

    if twin.recent_anomalies:
        specs.append(
            {
                "counterfactual_type": counterfactuals.COUNTERFACTUAL_REMOVE_ANOMALY_WINDOW,
                "params": {"anomaly_id": twin.recent_anomalies[0].anomaly_id},
            }
        )

    return specs


def _run_scenarios(
    specs: list[dict[str, Any]], *, tenant_id: str | None, twin: DigitalTwinState, seed: int, db_path: Path
) -> list[scenario_engine.ScenarioResult]:
    results: list[scenario_engine.ScenarioResult] = []
    for spec in specs:
        scenario_type = spec["scenario_type"]
        params = spec.get("params", {})
        started_at = utc_now()
        t0 = time.monotonic()
        status = "failed"
        try:
            result = scenario_engine.run_scenario(
                scenario_type,
                params,
                tenant_id=tenant_id,
                ticks=spec.get("ticks", scenario_engine.DEFAULT_TICKS),
                seed=spec.get("seed", seed),
                db_path=db_path,
                twin=twin,
                persist=True,
            )
            status = "success"
            results.append(result)
            print(
                f"  simulation_flow: scenario {scenario_type} complete "
                f"(gmv_delta={result.predicted_gmv_delta}, {time.monotonic() - t0:.1f}s)"
            )
        except Exception as exc:  # noqa: BLE001 - one bad scenario spec shouldn't block the rest of the batch
            _dispatch_simulation_failure("simulation_scenario_failure", scenario_type, exc)

        try:
            _append_simulation_run(
                db_path, kind="simulation_scenario", identifier=scenario_type,
                started_at=started_at, completed_at=utc_now(), status=status,
            )
        except Exception as append_exc:  # noqa: BLE001 - recording the run must never itself crash the flow
            print(f"  simulation_flow: could not record scenario run {scenario_type!r}: {append_exc!r}")
    return results


def _run_counterfactuals(
    specs: list[dict[str, Any]], *, seed: int, db_path: Path
) -> list[counterfactuals.CounterfactualResult]:
    results: list[counterfactuals.CounterfactualResult] = []
    for spec in specs:
        counterfactual_type = spec["counterfactual_type"]
        params = spec.get("params", {})
        started_at = utc_now()
        t0 = time.monotonic()
        status = "failed"
        try:
            result = counterfactuals.run_counterfactual(
                counterfactual_type,
                params,
                start_date=spec.get("start_date"),
                end_date=spec.get("end_date"),
                replay_ticks=spec.get("replay_ticks", counterfactuals.DEFAULT_REPLAY_TICKS),
                seed=spec.get("seed", seed),
                db_path=db_path,
                persist=True,
            )
            status = "success"
            results.append(result)
            print(
                f"  simulation_flow: counterfactual {counterfactual_type} complete "
                f"(gmv_delta={result.counterfactual_gmv_delta}, {time.monotonic() - t0:.1f}s)"
            )
        except Exception as exc:  # noqa: BLE001 - one bad counterfactual spec shouldn't block the rest of the batch
            _dispatch_simulation_failure("simulation_counterfactual_failure", counterfactual_type, exc)

        try:
            _append_simulation_run(
                db_path, kind="simulation_counterfactual", identifier=counterfactual_type,
                started_at=started_at, completed_at=utc_now(), status=status,
            )
        except Exception as append_exc:  # noqa: BLE001 - recording the run must never itself crash the flow
            print(f"  simulation_flow: could not record counterfactual run {counterfactual_type!r}: {append_exc!r}")
    return results


def run_simulation_flow(
    *,
    tenant_id: str | None = None,
    scenario_specs: list[dict[str, Any]] | None = None,
    counterfactual_specs: list[dict[str, Any]] | None = None,
    projection_ticks: int = scenario_engine.DEFAULT_TICKS,
    seed: int = 42,
    db_path: Path = DUCKDB_PATH,
) -> dict[str, Any]:
    """Runs one full simulation pass end to end - see module docstring for
    how each step maps onto PHASE8-SIMULATION.md Section 5's bullet list.
    `scenario_specs`/`counterfactual_specs` default to a small illustrative
    batch derived from whatever is actually in the warehouse (see
    `_default_scenario_specs()`/`_default_counterfactual_specs()`); pass
    explicit lists (e.g. from `api/simulation_api.py`'s `/simulation/run`)
    to run a caller-chosen batch instead."""
    if not db_path.exists():
        print("  simulation_flow: warehouse not built yet - run scripts/run_demo.py first")
        return {}

    print("  simulation_flow: loading digital twin...")
    twin = load_digital_twin(tenant_id, db_path)
    print(
        f"  simulation_flow: twin loaded - {len(twin.retailers)} retailer(s), {len(twin.products)} product(s), "
        f"{len(twin.recent_anomalies)} anomaly record(s), {len(twin.ml_predictions.forecasts)} forecast(s), "
        f"{len(twin.ml_predictions.clusters)} cluster row(s), {len(twin.ml_predictions.recommendations)} recommendation(s)"
    )

    print("  simulation_flow: loading agents...")
    _marketplace_agent, retailer_agents, product_agents = scenario_engine.build_agents(twin, db_path, seed=seed)
    print(f"  simulation_flow: agents loaded - 1 marketplace agent, {len(retailer_agents)} retailer agent(s), {len(product_agents)} product agent(s)")

    baseline_projection = scenario_engine.run_baseline_projection(twin, ticks=projection_ticks, seed=seed, db_path=db_path)
    print(f"  simulation_flow: baseline projection over {projection_ticks} tick(s) -> {baseline_projection}")

    if scenario_specs is None:
        scenario_specs = _default_scenario_specs(twin)
    if counterfactual_specs is None:
        counterfactual_specs = _default_counterfactual_specs(twin)

    scenario_results = _run_scenarios(scenario_specs, tenant_id=tenant_id, twin=twin, seed=seed, db_path=db_path)
    counterfactual_results = _run_counterfactuals(counterfactual_specs, seed=seed, db_path=db_path)

    return {
        "twin_summary": twin.to_summary_dict(),
        "baseline_projection": baseline_projection,
        "scenario_count": len(scenario_results),
        "counterfactual_count": len(counterfactual_results),
        "scenario_ids": [r.scenario_id for r in scenario_results],
        "counterfactual_ids": [r.counterfactual_id for r in counterfactual_results],
    }


if __name__ == "__main__":
    summary = run_simulation_flow()
    print(f"Simulation flow complete: {summary}")
