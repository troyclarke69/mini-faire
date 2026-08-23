"""Counterfactual Engine (PHASE8-SIMULATION.md Section 4, "what would have
happened if X didn't happen?").

Scope translation, same posture as `digital_twin.py`/`scenario_engine.py`'s
module docstrings: the spec's `warehouse/simulation/counterfactuals` is a
filesystem-path shape; this module writes to a `simulation.counterfactual_
results` DuckDB table instead, matching every other domain's schema-per-
domain convention.

Where `scenario_engine.py` asks "what if we changed something starting
*now* and ran forward" (a twin cloned from the live warehouse state, mutated,
then simulated), this module asks "what if something in the *past* had gone
differently" - it works directly on real historical rows from
`marts.fact_orders`, not the live twin. The spec's six bullet points map
onto this module's pipeline directly:

- "remove events from history" / "modify events" -> `_apply_counterfactual_
  filter()` builds a counterfactual order set by filtering out or rewriting
  a subset of the real `marts.fact_orders` rows (never mutating the
  original list - see that function's docstring).
- "recompute metrics" -> `_aggregate_retailers()`/`_aggregate_products()`
  re-derive the same order-count/net-revenue/retailer-health-score shape
  `compute/polars/transform_orders.py`'s `order_health_frame()` computes
  from real data (`_retailer_health_score()` is literally the same formula,
  imported from `scenario_engine.py` rather than redefined - see that
  import's comment), just run twice: once over the actual rows, once over
  the counterfactual rows. This module deliberately does NOT reach for a
  second `polars` pipeline to do this - the row counts involved (one
  tenant's historical order window) are small enough that plain Python
  aggregation is simpler to read and, not incidentally, easier to verify
  with this repo's stub-based test harness, and every other file in this
  `simulation/` package already favors plain dataclasses over `polars` for
  the same reason.
- "compare actual vs counterfactual" -> `_diff_retailer_aggregates()`/
  `_diff_product_aggregates()` produce the actual-vs-counterfactual deltas
  directly.
- "replay agent behavior" -> after recomputing the historical divergence,
  `run_counterfactual()` builds two `DigitalTwinState` snapshots "as of"
  the actual vs. counterfactual aggregates (`_build_twin_from_aggregates()`)
  and runs both forward `replay_ticks` seeded steps using the exact same
  `build_agents()`/`_run_ticks()` machinery `scenario_engine.py` uses for
  its own baseline-vs-treatment comparison - "replaying" the ABM forward
  from the point where the two histories diverge, to see whether that
  divergence compounds, fades, or reverses.
- "replay ML predictions" -> reuses `scenario_engine.py`'s `_predicted_
  anomalies()`/`_cluster_movement()`/`_predicted_recommendations()` proxies
  unchanged against the two post-replay twins - same documented "simplified,
  honest proxy, not a re-fit of ml/models/*" posture as that module, applied
  here instead of duplicated.

Reusing `scenario_engine.py`'s private (leading-underscore) helpers across
this sibling module is a deliberate choice, not an oversight: both files
live in the same `simulation` package and describe the same underlying
"clone a twin, run it forward with seeded agents, diff the result" idea -
duplicating that logic here would let the two drift out of sync with no
compile-time warning, which matters more than keeping the helpers private
to a single file.
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DUCKDB_PATH
from simulation.digital_twin import DigitalTwinState, MLPredictionSnapshot, ProductState, RetailerState
from simulation.scenario_engine import (
    _cluster_movement,
    _predicted_anomalies,
    _predicted_recommendations,
    _retailer_health_score,
    _run_ticks,
    build_agents,
)

COUNTERFACTUAL_REMOVE_RETAILER_ORDERS = "remove_retailer_orders"
COUNTERFACTUAL_REMOVE_PRODUCT_ORDERS = "remove_product_orders"
COUNTERFACTUAL_MODIFY_PRICE = "modify_price"
COUNTERFACTUAL_REMOVE_ANOMALY_WINDOW = "remove_anomaly_window"

COUNTERFACTUAL_TYPES = (
    COUNTERFACTUAL_REMOVE_RETAILER_ORDERS,
    COUNTERFACTUAL_REMOVE_PRODUCT_ORDERS,
    COUNTERFACTUAL_MODIFY_PRICE,
    COUNTERFACTUAL_REMOVE_ANOMALY_WINDOW,
)

# Mirrors scenario_engine.py's SCENARIO_PARAM_SCHEMA - surfaced by
# api/simulation_api.py's GET /simulation/counterfactuals so
# CounterfactualBuilder knows what fields to render.
COUNTERFACTUAL_PARAM_SCHEMA: dict[str, dict[str, str]] = {
    COUNTERFACTUAL_REMOVE_RETAILER_ORDERS: {
        "retailer_id": "required",
        "start_date": "optional (ISO date, default: no lower bound)",
        "end_date": "optional (ISO date, default: no upper bound)",
    },
    COUNTERFACTUAL_REMOVE_PRODUCT_ORDERS: {
        "product_id": "required",
        "start_date": "optional (ISO date, default: no lower bound)",
        "end_date": "optional (ISO date, default: no upper bound)",
    },
    COUNTERFACTUAL_MODIFY_PRICE: {
        "product_id": "required",
        "price_multiplier": "required",
        "start_date": "optional (ISO date, default: no lower bound)",
        "end_date": "optional (ISO date, default: no upper bound)",
    },
    COUNTERFACTUAL_REMOVE_ANOMALY_WINDOW: {
        "anomaly_id": "required",
        "window_hours": "optional (default 24 - +/- half this window around the anomaly's detected_at)",
    },
}

DEFAULT_REPLAY_TICKS = 14
DEFAULT_ANOMALY_WINDOW_HOURS = 24.0


class CounterfactualError(ValueError):
    """Raised for an unknown counterfactual_type, a missing/invalid param, or
    an anomaly_id that doesn't exist - NOT raised for a retailer_id/
    product_id that simply has no matching orders in the window, since
    "this entity had zero orders here" is a legitimate real-data answer for
    a remove/modify counterfactual, not an input error (contrast with
    scenario_engine.py's ScenarioError, which does reject an unknown
    product/retailer_id, because there the twin's retailer/product set is
    small and fully known up front - see build_agents())."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise CounterfactualError(f"invalid date value {value!r}") from exc
    raise CounterfactualError(f"invalid date value {value!r}")


def _matches_date_window(order_date_value: date | None, start_date: date | None, end_date: date | None) -> bool:
    if order_date_value is None:
        return False
    if start_date is not None and order_date_value < start_date:
        return False
    if end_date is not None and order_date_value > end_date:
        return False
    return True


def _load_actual_order_rows(db_path: Path, start_date: date | None, end_date: date | None) -> list[dict[str, Any]]:
    """Real historical rows from `marts.fact_orders` - see module docstring
    for why this stays plain dicts rather than a polars frame. Wrapped to
    degrade to an empty list on any failure (missing table on a fresh
    warehouse, etc.), matching `digital_twin.py`'s "missing table == nothing
    to show, not a crash" convention throughout this phase."""
    sql = (
        "select order_id, retailer_id, product_id, order_ts, order_date, quantity, gross_amount, "
        "discount_amount, net_amount, estimated_cost, estimated_profit, status from marts.fact_orders"
    )
    clauses = []
    params: list[Any] = []
    if start_date is not None:
        clauses.append("order_date >= ?")
        params.append(start_date)
    if end_date is not None:
        clauses.append("order_date <= ?")
        params.append(end_date)
    if clauses:
        sql += " where " + " and ".join(clauses)
    try:
        with connect_with_retry(db_path, read_only=True) as con:
            result = con.execute(sql, params)
            cols = [c[0] for c in result.description]
            return [dict(zip(cols, row)) for row in result.fetchall()]
    except Exception:
        return []


def _lookup_anomaly_window(anomaly_id: str, db_path: Path, window_hours: float) -> dict[str, Any]:
    try:
        with connect_with_retry(db_path, read_only=True) as con:
            rows = con.execute(
                "select entity_type, entity_id, detected_at from anomalies.anomaly_events where anomaly_id = ?",
                [anomaly_id],
            ).fetchall()
    except Exception:
        rows = []
    if not rows:
        raise CounterfactualError(f"unknown anomaly_id {anomaly_id!r}")
    entity_type, entity_id, detected_at = rows[0]
    half = timedelta(hours=window_hours / 2)
    return {"entity_type": entity_type, "entity_id": entity_id, "start": detected_at - half, "end": detected_at + half}


def _apply_counterfactual_filter(
    counterfactual_type: str, params: dict[str, Any], orders: list[dict[str, Any]], db_path: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Builds the counterfactual order set. `orders` itself is never mutated
    (a row that needs its price rewritten is shallow-copied first), so a
    caller can keep using the original `orders` list as "what actually
    happened" after this returns. `meta["affected_order_ids"]` records
    exactly which real orders were removed or modified, so a scenario result
    can show its work rather than just a before/after number."""
    meta: dict[str, Any] = {"affected_order_ids": []}

    if counterfactual_type == COUNTERFACTUAL_REMOVE_RETAILER_ORDERS:
        retailer_id = params["retailer_id"]
        start_date = _coerce_date(params.get("start_date"))
        end_date = _coerce_date(params.get("end_date"))
        kept = []
        for order in orders:
            if order["retailer_id"] == retailer_id and _matches_date_window(order["order_date"], start_date, end_date):
                meta["affected_order_ids"].append(order["order_id"])
                continue
            kept.append(order)
        return kept, meta

    if counterfactual_type == COUNTERFACTUAL_REMOVE_PRODUCT_ORDERS:
        product_id = params["product_id"]
        start_date = _coerce_date(params.get("start_date"))
        end_date = _coerce_date(params.get("end_date"))
        kept = []
        for order in orders:
            if order["product_id"] == product_id and _matches_date_window(order["order_date"], start_date, end_date):
                meta["affected_order_ids"].append(order["order_id"])
                continue
            kept.append(order)
        return kept, meta

    if counterfactual_type == COUNTERFACTUAL_MODIFY_PRICE:
        product_id = params["product_id"]
        multiplier = float(params["price_multiplier"])
        start_date = _coerce_date(params.get("start_date"))
        end_date = _coerce_date(params.get("end_date"))
        modified = []
        for order in orders:
            if order["product_id"] == product_id and _matches_date_window(order["order_date"], start_date, end_date):
                order = dict(order)
                new_gross = round(float(order["gross_amount"] or 0.0) * multiplier, 2)
                discount = float(order["discount_amount"] or 0.0)
                new_net = round(new_gross - discount, 2)
                order["gross_amount"] = new_gross
                order["net_amount"] = new_net
                # quantity/estimated_cost held fixed here on purpose - this
                # retrospective recompute isolates the pure price-mechanical
                # effect on historical revenue. Whether a different price
                # would ALSO have moved historical quantity (elasticity) is
                # exactly what the forward "replay agent behavior" step
                # below models instead, via product_agent.py's own
                # elasticity curve - not duplicated here.
                order["estimated_profit"] = round(new_net - float(order["estimated_cost"] or 0.0), 2)
                meta["affected_order_ids"].append(order["order_id"])
            modified.append(order)
        return modified, meta

    if counterfactual_type == COUNTERFACTUAL_REMOVE_ANOMALY_WINDOW:
        window_hours = float(params.get("window_hours", DEFAULT_ANOMALY_WINDOW_HOURS))
        window = _lookup_anomaly_window(params["anomaly_id"], db_path, window_hours)
        entity_type, entity_id = window["entity_type"], window["entity_id"]
        start, end = window["start"], window["end"]
        kept = []
        for order in orders:
            ts = order["order_ts"]
            entity_matches = (entity_type == "retailer" and order["retailer_id"] == entity_id) or (
                entity_type == "product" and order["product_id"] == entity_id
            )
            if entity_matches and ts is not None and start <= ts <= end:
                meta["affected_order_ids"].append(order["order_id"])
                continue
            kept.append(order)
        meta["anomaly_window"] = {"entity_type": entity_type, "entity_id": entity_id, "start": str(start), "end": str(end)}
        return kept, meta

    raise CounterfactualError(f"unknown counterfactual_type {counterfactual_type!r} - must be one of {COUNTERFACTUAL_TYPES}")


def _aggregate_retailers(orders: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = {}
    for order in orders:
        bucket = agg.setdefault(
            order["retailer_id"], {"order_count": 0, "net_revenue": 0.0, "estimated_profit": 0.0, "last_order_ts": None}
        )
        bucket["order_count"] += 1
        bucket["net_revenue"] += float(order["net_amount"] or 0.0)
        bucket["estimated_profit"] += float(order["estimated_profit"] or 0.0)
        ts = order["order_ts"]
        if ts is not None and (bucket["last_order_ts"] is None or ts > bucket["last_order_ts"]):
            bucket["last_order_ts"] = ts
    for bucket in agg.values():
        bucket["retailer_health_score"] = _retailer_health_score(
            bucket["order_count"], bucket["net_revenue"], bucket["estimated_profit"]
        )
    return agg


def _aggregate_products(orders: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = {}
    for order in orders:
        bucket = agg.setdefault(order["product_id"], {"units_sold": 0, "net_revenue": 0.0, "last_sold_at": None})
        bucket["units_sold"] += int(order["quantity"] or 0)
        bucket["net_revenue"] += float(order["net_amount"] or 0.0)
        ts = order["order_ts"]
        if ts is not None and (bucket["last_sold_at"] is None or ts > bucket["last_sold_at"]):
            bucket["last_sold_at"] = ts
    return agg


def _diff_retailer_aggregates(
    actual: dict[str, dict[str, Any]], counterfactual: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    empty = {"order_count": 0, "net_revenue": 0.0, "retailer_health_score": 0.0}
    diffs = []
    for retailer_id in set(actual) | set(counterfactual):
        a = actual.get(retailer_id, empty)
        c = counterfactual.get(retailer_id, empty)
        diffs.append(
            {
                "retailer_id": retailer_id,
                "order_count_actual": a["order_count"],
                "order_count_counterfactual": c["order_count"],
                "net_revenue_actual": round(a["net_revenue"], 2),
                "net_revenue_counterfactual": round(c["net_revenue"], 2),
                "net_revenue_delta": round(c["net_revenue"] - a["net_revenue"], 2),
                "retailer_health_score_actual": a.get("retailer_health_score", 0.0),
                "retailer_health_score_counterfactual": c.get("retailer_health_score", 0.0),
            }
        )
    diffs.sort(key=lambda d: abs(d["net_revenue_delta"]), reverse=True)
    return diffs


def _diff_product_aggregates(
    actual: dict[str, dict[str, Any]], counterfactual: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    empty = {"units_sold": 0, "net_revenue": 0.0}
    diffs = []
    for product_id in set(actual) | set(counterfactual):
        a = actual.get(product_id, empty)
        c = counterfactual.get(product_id, empty)
        diffs.append(
            {
                "product_id": product_id,
                "units_sold_actual": a["units_sold"],
                "units_sold_counterfactual": c["units_sold"],
                "units_sold_delta": c["units_sold"] - a["units_sold"],
                "net_revenue_actual": round(a["net_revenue"], 2),
                "net_revenue_counterfactual": round(c["net_revenue"], 2),
                "net_revenue_delta": round(c["net_revenue"] - a["net_revenue"], 2),
            }
        )
    diffs.sort(key=lambda d: abs(d["net_revenue_delta"]), reverse=True)
    return diffs


def _build_twin_from_aggregates(
    retailer_agg: dict[str, dict[str, Any]], product_agg: dict[str, dict[str, Any]], db_path: Path
) -> DigitalTwinState:
    """Builds a `DigitalTwinState` "as of" a recomputed order aggregate,
    joined against `marts.dim_retailer`/`dim_product` for the descriptive
    fields (name/category/current price/current inventory) `marts.
    fact_orders` itself doesn't carry - the same "join facts onto dims"
    shape `digital_twin.py`'s `_load_classic_twin()` uses for the live twin,
    fed from these in-memory aggregates instead of `compute_retailer_health`/
    `compute_product_reorder_risk`, since a counterfactual's recomputed order
    set has no corresponding `compute_*` table of its own. `inventory_count`
    is read as today's current value rather than reconstructed retroactively
    (e.g. "this product would have more stock left if fewer orders had
    shipped") - modeling that divergence is out of scope for this phase and
    would compound with the forward replay below in a way that's hard to
    reason about; documented here rather than silently assumed away."""
    try:
        with connect_with_retry(db_path, read_only=True) as con:
            retailer_dims = con.execute(
                "select retailer_id, retailer_name, country, retailer_category, status from marts.dim_retailer"
            ).fetchall()
            product_dims = con.execute(
                "select product_id, product_name, product_category, brand_id, unit_price, unit_cost, "
                "inventory_count, is_active from marts.dim_product"
            ).fetchall()
    except Exception:
        retailer_dims, product_dims = [], []

    retailers: dict[str, RetailerState] = {}
    for retailer_id, name, country, category, status in retailer_dims:
        agg = retailer_agg.get(retailer_id, {})
        retailers[retailer_id] = RetailerState(
            retailer_id=retailer_id,
            retailer_name=name,
            country=country,
            retailer_category=category,
            status=status,
            order_count=int(agg.get("order_count", 0)),
            net_revenue=float(agg.get("net_revenue", 0.0)),
            estimated_profit=agg.get("estimated_profit"),
            last_order_ts=agg.get("last_order_ts"),
            retailer_health_score=agg.get("retailer_health_score"),
        )
    for retailer_id, agg in retailer_agg.items():
        # a retailer with orders but missing from dim_retailer shouldn't
        # happen against real data, but degrade gracefully rather than
        # silently dropping its activity from the twin.
        if retailer_id not in retailers:
            retailers[retailer_id] = RetailerState(
                retailer_id=retailer_id,
                retailer_name=None,
                country=None,
                retailer_category=None,
                status="active",
                order_count=int(agg.get("order_count", 0)),
                net_revenue=float(agg.get("net_revenue", 0.0)),
                estimated_profit=agg.get("estimated_profit"),
                last_order_ts=agg.get("last_order_ts"),
                retailer_health_score=agg.get("retailer_health_score"),
            )

    products: dict[str, ProductState] = {}
    for product_id, name, category, brand_id, unit_price, unit_cost, inventory_count, is_active in product_dims:
        agg = product_agg.get(product_id, {})
        products[product_id] = ProductState(
            product_id=product_id,
            product_name=name,
            product_category=category,
            brand_id=brand_id,
            unit_price=unit_price,
            unit_cost=unit_cost,
            inventory_count=inventory_count,
            is_active=is_active,
            units_sold=int(agg.get("units_sold", 0)),
            inventory_velocity=None,
            reorder_risk_score=None,
            reorder_risk_band=None,
            last_sold_at=agg.get("last_sold_at"),
        )
    for product_id, agg in product_agg.items():
        if product_id not in products:
            products[product_id] = ProductState(
                product_id=product_id,
                product_name=None,
                product_category=None,
                brand_id=None,
                unit_price=None,
                unit_cost=None,
                inventory_count=None,
                is_active=None,
                units_sold=int(agg.get("units_sold", 0)),
                inventory_velocity=None,
                reorder_risk_score=None,
                reorder_risk_band=None,
                last_sold_at=agg.get("last_sold_at"),
            )

    return DigitalTwinState(
        tenant_id=None,
        as_of=_utc_now(),
        retailers=retailers,
        products=products,
        recent_anomalies=[],
        ml_predictions=MLPredictionSnapshot(),
    )


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists simulation")
    con.execute(
        """
        create table if not exists simulation.counterfactual_results (
          counterfactual_id varchar primary key,
          counterfactual_type varchar,
          tenant_id varchar,
          params varchar,
          replay_ticks integer,
          actual_order_count integer,
          counterfactual_order_count integer,
          actual_units_sold integer,
          counterfactual_units_sold integer,
          actual_gmv double,
          counterfactual_gmv double,
          counterfactual_gmv_delta double,
          retailer_diffs varchar,
          product_diffs varchar,
          predicted_anomalies varchar,
          predicted_cluster_movement varchar,
          predicted_recommendations varchar,
          removed_or_modified_order_ids varchar,
          started_at timestamptz,
          completed_at timestamptz,
          status varchar
        )
        """
    )


@dataclass
class CounterfactualResult:
    counterfactual_id: str
    counterfactual_type: str
    # Counterfactuals currently only operate on the classic single-tenant
    # warehouse (marts.fact_orders) - there is no per-tenant equivalent of
    # that fact table yet (same gap digital_twin.py's module docstring
    # already documents for the tenant twin). Always None today; kept as a
    # field for symmetry with ScenarioResult's persisted shape and so a
    # future per-tenant fact_orders doesn't require a schema migration.
    tenant_id: str | None
    params: dict[str, Any]
    replay_ticks: int
    actual_order_count: int
    counterfactual_order_count: int
    actual_units_sold: int
    counterfactual_units_sold: int
    actual_gmv: float
    counterfactual_gmv: float
    counterfactual_gmv_delta: float
    retailer_diffs: list[dict]
    product_diffs: list[dict]
    predicted_anomalies: list[dict]
    predicted_cluster_movement: list[dict]
    predicted_recommendations: list[dict]
    removed_or_modified_order_ids: list[str]
    started_at: str
    completed_at: str
    status: str


def run_counterfactual(
    counterfactual_type: str,
    params: dict[str, Any],
    *,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    replay_ticks: int = DEFAULT_REPLAY_TICKS,
    seed: int = 42,
    db_path: Path = DUCKDB_PATH,
    persist: bool = True,
) -> CounterfactualResult:
    """Runs one counterfactual end to end: load the real historical order
    window, build the counterfactual order set, recompute + diff metrics,
    then replay both the actual and counterfactual histories forward
    `replay_ticks` seeded steps via the same agents `scenario_engine.py`
    uses, so the result covers every bullet in the spec's Section 4 (see
    module docstring for the mapping). `start_date`/`end_date` scope which
    historical orders are even in play; a counterfactual whose filter
    matches nothing in that window is not an error - it means "this
    counterfactual would have changed nothing here", which is itself a
    real, useful answer for a what-if tool."""
    if counterfactual_type not in COUNTERFACTUAL_TYPES:
        raise CounterfactualError(f"unknown counterfactual_type {counterfactual_type!r} - must be one of {COUNTERFACTUAL_TYPES}")

    started_at = utc_now()
    coerced_start = _coerce_date(start_date)
    coerced_end = _coerce_date(end_date)

    actual_orders = _load_actual_order_rows(db_path, coerced_start, coerced_end)
    counterfactual_orders, meta = _apply_counterfactual_filter(counterfactual_type, params, actual_orders, db_path)

    actual_retailer_agg = _aggregate_retailers(actual_orders)
    counterfactual_retailer_agg = _aggregate_retailers(counterfactual_orders)
    actual_product_agg = _aggregate_products(actual_orders)
    counterfactual_product_agg = _aggregate_products(counterfactual_orders)

    retailer_diffs = _diff_retailer_aggregates(actual_retailer_agg, counterfactual_retailer_agg)
    product_diffs = _diff_product_aggregates(actual_product_agg, counterfactual_product_agg)

    actual_twin = _build_twin_from_aggregates(actual_retailer_agg, actual_product_agg, db_path)
    counterfactual_twin = _build_twin_from_aggregates(counterfactual_retailer_agg, counterfactual_product_agg, db_path)

    if replay_ticks > 0:
        actual_marketplace, actual_retailers, _ = build_agents(actual_twin, db_path, seed=seed)
        cf_marketplace, cf_retailers, _ = build_agents(counterfactual_twin, db_path, seed=seed)
        _run_ticks(actual_twin, actual_marketplace, actual_retailers, replay_ticks, random.Random(seed))
        _run_ticks(counterfactual_twin, cf_marketplace, cf_retailers, replay_ticks, random.Random(seed))

    actual_gmv = sum(float(o["net_amount"] or 0.0) for o in actual_orders)
    counterfactual_gmv = sum(float(o["net_amount"] or 0.0) for o in counterfactual_orders)
    completed_at = utc_now()

    result = CounterfactualResult(
        counterfactual_id=str(uuid.uuid4()),
        counterfactual_type=counterfactual_type,
        tenant_id=None,
        params=params,
        replay_ticks=replay_ticks,
        actual_order_count=len(actual_orders),
        counterfactual_order_count=len(counterfactual_orders),
        actual_units_sold=sum(int(o["quantity"] or 0) for o in actual_orders),
        counterfactual_units_sold=sum(int(o["quantity"] or 0) for o in counterfactual_orders),
        actual_gmv=round(actual_gmv, 2),
        counterfactual_gmv=round(counterfactual_gmv, 2),
        counterfactual_gmv_delta=round(counterfactual_gmv - actual_gmv, 2),
        retailer_diffs=retailer_diffs,
        product_diffs=product_diffs,
        predicted_anomalies=_predicted_anomalies(actual_twin, counterfactual_twin),
        predicted_cluster_movement=_cluster_movement(actual_twin, counterfactual_twin),
        predicted_recommendations=_predicted_recommendations(counterfactual_twin),
        removed_or_modified_order_ids=meta.get("affected_order_ids", []),
        started_at=started_at,
        completed_at=completed_at,
        status="success",
    )

    if persist:
        persist_counterfactual_result(result, db_path)
    return result


def persist_counterfactual_result(result: CounterfactualResult, db_path: Path = DUCKDB_PATH) -> None:
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.execute(
            """
            insert into simulation.counterfactual_results values
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.counterfactual_id,
                result.counterfactual_type,
                result.tenant_id,
                json.dumps(result.params, default=str),
                result.replay_ticks,
                result.actual_order_count,
                result.counterfactual_order_count,
                result.actual_units_sold,
                result.counterfactual_units_sold,
                result.actual_gmv,
                result.counterfactual_gmv,
                result.counterfactual_gmv_delta,
                json.dumps(result.retailer_diffs),
                json.dumps(result.product_diffs),
                json.dumps(result.predicted_anomalies),
                json.dumps(result.predicted_cluster_movement),
                json.dumps(result.predicted_recommendations),
                json.dumps(result.removed_or_modified_order_ids),
                result.started_at,
                result.completed_at,
                result.status,
            ],
        )
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=f"counterfactual_{result.counterfactual_id}",
                source_node="marts.fact_orders",
                target_node="simulation.counterfactual_results",
                edge_type="counterfactual_simulated",
                entity=result.counterfactual_type,
                created_at=result.completed_at,
            )
        ],
        db_path,
    )


def list_counterfactual_results(*, limit: int = 50, db_path: Path = DUCKDB_PATH) -> list[dict]:
    try:
        with connect_with_retry(db_path, read_only=True) as con:
            result = con.execute(
                "select * from simulation.counterfactual_results order by completed_at desc limit ?", [limit]
            )
            cols = [c[0] for c in result.description]
            return [dict(zip(cols, row)) for row in result.fetchall()]
    except Exception:
        return []


if __name__ == "__main__":
    # A retailer_id that (almost certainly) matches nothing is a safe demo
    # against any real warehouse - see run_counterfactual()'s docstring on
    # why "matched nothing" is a valid, non-error result here.
    demo_result = run_counterfactual(
        COUNTERFACTUAL_REMOVE_RETAILER_ORDERS, {"retailer_id": "__demo_missing_retailer__"}, persist=False
    )
    print(json.dumps(demo_result.__dict__, default=str, indent=2))
