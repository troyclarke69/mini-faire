"""Digital Twin Core Engine (PHASE8-SIMULATION.md Section 1).

Scope translation, stated plainly rather than left implicit (same posture
this repo already takes throughout Phase 7 - see e.g. `multi_tenant/
tenant_manager.py`'s module docstring): the spec asks for a twin that
"maintains state" across nine dimensions and "updates per event / per
synthetic tick / per real-time ingestion / per simulation step". This module
does NOT stand up a second, parallel state store that duplicates ingestion/
ELT/compute - that would drift from the real warehouse the moment either
side changed. Instead:

- `load_digital_twin()` builds a `DigitalTwinState` snapshot by READING the
  warehouse tables Phases 3-7 already populate and keep correct
  (`marts.dim_retailer`/`dim_product`/`compute_retailer_health`/
  `compute_product_reorder_risk`, `anomalies.anomaly_events`, `ml.forecasts`/
  `clusters`/`recommendations`/`anomaly_classifications`) - this covers
  "maintain state for each retailer/product", "inventory levels", "pricing
  curves" (unit_price history isn't tracked yet - see ProductState's
  docstring), "anomaly history", and "ML predictions" from the spec's list.
  "Demand curves" and "event lifecycle states" are derived on top (see
  `ProductState.demand_curve`/`retailer_agent.py`'s use of
  `marts.compute_order_lifecycle`), not separate storage.
- "Update per real-time ingestion" = call `load_digital_twin()` again (or
  `DigitalTwinState.refresh()`) - the same "re-read, don't diff" pattern
  `orchestration/realtime_flow.py` already uses for the rest of this app.
- "Update per event" / "update per simulation step" = the mutation methods
  below (`apply_order`, `apply_price_change`, `apply_inventory_delta`,
  `advance_tick`) operate purely in-memory on a *cloned* state, so
  `simulation/scenario_engine.py` and `simulation/counterfactuals.py` can run
  many forward steps without ever touching the real warehouse mid-run - only
  `orchestration/simulation_flow.py` persists a run's final results.
- "Maintain tenant isolation": `tenant_id=None` loads the classic
  single-tenant demo warehouse (the full nine-dimension twin below).
  `tenant_id=<id>` loads a narrower twin scoped to that tenant's own tables
  (`marts.fact_tenant_orders`/`metrics_tenant_daily`/`compute_tenant_health`/
  `compute_tenant_growth` - see `ingestion/tenant_ingest.py`'s module
  docstring for why "orders" is the only entity carried through the tenant
  pipeline). A tenant twin's retailers/products are therefore bare IDs with
  no name/category/pricing/inventory/anomaly/ML richness attached, since no
  per-tenant equivalent of `dim_retailer`/`dim_product`/`anomalies.*`/`ml.*`
  exists yet (same gap `multi_tenant/tenant_manager.py`'s docstring already
  documents for silo schemas) - `RetailerState`/`ProductState` fields that
  don't apply to a tenant twin are `None`, not fabricated.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingestion.duckdb_utils import connect_with_retry
from ingestion.paths import DUCKDB_PATH


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_float(value: Any) -> float | None:
    """DuckDB returns `decimal(N, 2)` columns (`unit_price`/`unit_cost` on
    `marts.dim_product`, etc.) as Python `decimal.Decimal` objects, not
    floats - even though every numeric field on `RetailerState`/`ProductState`
    below is typed `float | None`. Mixing a raw `Decimal` into ordinary float
    arithmetic downstream (`product_agent.py`'s `price_ratio ** elasticity`,
    `retailer_agent.py`'s `price *= 1.0 - promotion_discount`) raises
    `TypeError: unsupported operand type(s) for ** or pow(): 'decimal.
    Decimal' and 'float'` (or the `*=` equivalent) - so every DECIMAL-sourced
    numeric value is normalized to a plain float once, right here at the
    warehouse boundary, rather than leaving each downstream call site to
    guard against a type the dataclass already claims can't occur. Same
    posture `counterfactuals.py`'s `_apply_counterfactual_filter()`/
    `_aggregate_retailers()` already take for `gross_amount`/`net_amount`/
    `estimated_profit`."""
    if value is None:
        return None
    return float(value)


@dataclass
class ProductState:
    product_id: str
    product_name: str | None
    product_category: str | None
    brand_id: str | None
    unit_price: float | None
    unit_cost: float | None
    inventory_count: int | None
    is_active: bool | None
    units_sold: int
    inventory_velocity: float | None
    reorder_risk_score: float | None
    reorder_risk_band: str | None
    last_sold_at: datetime | None

    def demand_curve_point(self) -> float:
        """A single scalar "how fast is this selling relative to what's on
        the shelf" reading - `inventory_velocity` if the warehouse already
        computed one (`marts.metrics_product_velocity`), else derived from
        units_sold/inventory_count directly. Not a fitted curve (no elasticity
        model lives here - see `simulation/agents/product_agent.py` for
        where price elasticity is actually applied), just the one number
        `product_agent.py`'s demand-response strategy reacts to per tick."""
        if self.inventory_velocity is not None:
            return self.inventory_velocity
        if not self.inventory_count:
            return 0.0
        return self.units_sold / self.inventory_count


@dataclass
class RetailerState:
    retailer_id: str
    retailer_name: str | None
    country: str | None
    retailer_category: str | None
    status: str | None
    order_count: int
    net_revenue: float
    estimated_profit: float | None
    last_order_ts: datetime | None
    retailer_health_score: float | None


@dataclass
class AnomalyRecord:
    anomaly_id: str
    anomaly_type: str
    severity: str
    detected_at: datetime | None
    entity_type: str
    entity_id: str
    metric_name: str
    metric_value: float | None
    baseline_value: float | None
    deviation: float | None


@dataclass
class MLPredictionSnapshot:
    forecasts: list[dict] = field(default_factory=list)
    clusters: list[dict] = field(default_factory=list)
    recommendations: list[dict] = field(default_factory=list)
    anomaly_classifications: list[dict] = field(default_factory=list)


@dataclass
class DigitalTwinState:
    tenant_id: str | None
    as_of: datetime
    retailers: dict[str, RetailerState] = field(default_factory=dict)
    products: dict[str, ProductState] = field(default_factory=dict)
    recent_anomalies: list[AnomalyRecord] = field(default_factory=list)
    ml_predictions: MLPredictionSnapshot = field(default_factory=MLPredictionSnapshot)
    tick: int = 0
    # Free-form per-tick log ("what happened") - agents/scenario_engine.py
    # append short strings here so a scenario's results can show a timeline,
    # not just a before/after diff. Not persisted verbatim to the warehouse;
    # scenario_engine.py summarizes it into simulation.scenario_results.
    event_log: list[str] = field(default_factory=list)

    def clone(self) -> "DigitalTwinState":
        """A fully independent copy safe to run forward ticks against
        without mutating the source snapshot - what scenario_engine.py and
        counterfactuals.py both branch from before diverging a simulation
        away from "what actually happened"."""
        return copy.deepcopy(self)

    # -- in-memory mutation, one per PHASE8-SIMULATION.md's "update per
    # event"/"update per simulation step" - none of these touch DuckDB.

    def apply_order(self, retailer_id: str, product_id: str, quantity: int, gross_amount: float, net_amount: float) -> None:
        retailer = self.retailers.get(retailer_id)
        if retailer is not None:
            retailer.order_count += 1
            retailer.net_revenue += net_amount
            retailer.last_order_ts = self.as_of
        product = self.products.get(product_id)
        if product is not None:
            product.units_sold += quantity
            if product.inventory_count is not None:
                product.inventory_count = max(0, product.inventory_count - quantity)
            product.last_sold_at = self.as_of
        self.event_log.append(
            f"tick={self.tick} order retailer={retailer_id} product={product_id} qty={quantity} net={net_amount:.2f}"
        )

    def apply_price_change(self, product_id: str, new_price: float) -> None:
        product = self.products.get(product_id)
        if product is None:
            return
        old_price = product.unit_price
        product.unit_price = new_price
        self.event_log.append(f"tick={self.tick} price_change product={product_id} {old_price} -> {new_price}")

    def apply_inventory_delta(self, product_id: str, delta: int) -> None:
        product = self.products.get(product_id)
        if product is None:
            return
        base = product.inventory_count or 0
        product.inventory_count = max(0, base + delta)
        self.event_log.append(f"tick={self.tick} inventory_delta product={product_id} delta={delta}")

    def apply_retailer_status(self, retailer_id: str, status: str) -> None:
        retailer = self.retailers.get(retailer_id)
        if retailer is None:
            return
        self.event_log.append(f"tick={self.tick} retailer_status retailer={retailer_id} {retailer.status} -> {status}")
        retailer.status = status

    def advance_tick(self) -> None:
        self.tick += 1

    # -- aggregate readouts scenario_engine.py/counterfactuals.py compare
    # actual-vs-simulated on. Deliberately the same shape as the metrics
    # already surfaced elsewhere (marts.metrics_retailer_daily's gmv/
    # net_revenue, marts.compute_product_reorder_risk's inventory fields) so
    # a scenario's "predicted GMV" is directly comparable to a real one.

    def total_gmv(self) -> float:
        return sum(r.net_revenue for r in self.retailers.values())

    def total_units_sold(self) -> int:
        return sum(p.units_sold for p in self.products.values())

    def average_inventory_velocity(self) -> float | None:
        velocities = [p.demand_curve_point() for p in self.products.values()]
        return sum(velocities) / len(velocities) if velocities else None

    def to_summary_dict(self) -> dict[str, Any]:
        """Compact, JSON-safe summary - what api/simulation_api.py's
        `/simulation/state` returns and what scenario_engine.py/
        counterfactuals.py snapshot into their persisted result rows
        (`simulation.scenario_results.result_summary` /
        `simulation.counterfactual_results.*_summary`, both `varchar` columns
        of this dict's `json.dumps()` - same "store as a JSON string in a
        varchar column" convention `multi_tenant/tenant_manager.py`'s
        `metadata` column and `ml/registry.py`'s `params`/`metrics` columns
        already use)."""
        return {
            "tenant_id": self.tenant_id,
            "as_of": self.as_of.isoformat(),
            "tick": self.tick,
            "retailer_count": len(self.retailers),
            "product_count": len(self.products),
            "gmv": self.total_gmv(),
            "units_sold": self.total_units_sold(),
            "average_inventory_velocity": self.average_inventory_velocity(),
            "open_anomaly_count": len(self.recent_anomalies),
            "active_forecast_count": len(self.ml_predictions.forecasts),
        }


def _row_to_retailer(row: dict) -> RetailerState:
    return RetailerState(
        retailer_id=row["retailer_id"],
        retailer_name=row.get("retailer_name"),
        country=row.get("country"),
        retailer_category=row.get("retailer_category"),
        status=row.get("status"),
        order_count=int(row.get("order_count") or 0),
        net_revenue=float(row.get("net_revenue") or 0.0),
        estimated_profit=_to_float(row.get("estimated_profit")),
        last_order_ts=row.get("last_order_ts"),
        retailer_health_score=_to_float(row.get("retailer_health_score")),
    )


def _row_to_product(row: dict, velocity_by_id: dict[str, float]) -> ProductState:
    return ProductState(
        product_id=row["product_id"],
        product_name=row.get("product_name"),
        product_category=row.get("product_category"),
        brand_id=row.get("brand_id"),
        unit_price=_to_float(row.get("unit_price")),
        unit_cost=_to_float(row.get("unit_cost")),
        inventory_count=row.get("inventory_count"),
        is_active=row.get("is_active"),
        units_sold=int(row.get("units_sold") or 0),
        inventory_velocity=velocity_by_id.get(row["product_id"]),
        reorder_risk_score=_to_float(row.get("reorder_risk_score")),
        reorder_risk_band=row.get("reorder_risk_band"),
        last_sold_at=row.get("last_sold_at"),
    )


def _load_classic_twin(con) -> tuple[dict[str, RetailerState], dict[str, ProductState]]:
    # `result.description`/`result.fetchall()` off the object execute()
    # returns - not `con.description` (con has no such attribute; matches
    # compute/polars/duckdb_frame.py's read_duckdb_frame()'s existing pattern).
    retailer_result = con.execute(
        """
        select d.retailer_id, d.retailer_name, d.country, d.retailer_category, d.status,
               coalesce(h.order_count, 0) as order_count, coalesce(h.net_revenue, 0.0) as net_revenue,
               h.estimated_profit, h.last_order_ts, h.retailer_health_score
        from marts.dim_retailer d
        left join marts.compute_retailer_health h on d.retailer_id = h.retailer_id
        """
    )
    retailer_cols = [c[0] for c in retailer_result.description]
    retailers = {
        row[0]: _row_to_retailer(dict(zip(retailer_cols, row))) for row in retailer_result.fetchall()
    }

    velocity_rows = con.execute(
        "select product_id, inventory_velocity from marts.metrics_product_velocity"
    ).fetchall()
    velocity_by_id = {r[0]: r[1] for r in velocity_rows if r[1] is not None}

    product_result = con.execute(
        """
        select d.product_id, d.product_name, d.product_category, d.brand_id, d.unit_price,
               d.unit_cost, d.inventory_count, d.is_active,
               coalesce(rr.units_sold, 0) as units_sold, rr.reorder_risk_score,
               rr.reorder_risk_band, rr.last_sold_at
        from marts.dim_product d
        left join marts.compute_product_reorder_risk rr on d.product_id = rr.product_id
        """
    )
    product_cols = [c[0] for c in product_result.description]
    products = {
        row[0]: _row_to_product(dict(zip(product_cols, row)), velocity_by_id) for row in product_result.fetchall()
    }
    return retailers, products


def _load_tenant_twin(con, tenant_id: str) -> tuple[dict[str, RetailerState], dict[str, ProductState]]:
    """Narrower twin for a real tenant - see module docstring. Built from
    `marts.fact_tenant_orders` directly (grouping by retailer_id/product_id)
    rather than `marts.compute_tenant_health`/`compute_tenant_growth`, since
    those are tenant-level (one row per tenant), not per-retailer/product -
    there is no per-tenant-per-retailer or per-tenant-per-product compute
    table yet, so this derives the closest equivalent inline instead of
    silently reusing the wrong grain."""
    try:
        retailer_rows = con.execute(
            """
            select retailer_id, count(distinct order_id) as order_count,
                   sum(net_amount) as net_revenue, max(order_ts) as last_order_ts
            from marts.fact_tenant_orders
            where tenant_id = ?
            group by retailer_id
            """,
            [tenant_id],
        ).fetchall()
    except Exception:
        retailer_rows = []
    retailers = {
        row[0]: RetailerState(
            retailer_id=row[0], retailer_name=None, country=None, retailer_category=None, status=None,
            order_count=int(row[1] or 0), net_revenue=float(row[2] or 0.0), estimated_profit=None,
            last_order_ts=row[3], retailer_health_score=None,
        )
        for row in retailer_rows
    }

    try:
        product_rows = con.execute(
            """
            select product_id, sum(quantity) as units_sold, max(order_ts) as last_sold_at
            from marts.fact_tenant_orders
            where tenant_id = ?
            group by product_id
            """,
            [tenant_id],
        ).fetchall()
    except Exception:
        product_rows = []
    products = {
        row[0]: ProductState(
            product_id=row[0], product_name=None, product_category=None, brand_id=None,
            unit_price=None, unit_cost=None, inventory_count=None, is_active=None,
            units_sold=int(row[1] or 0), inventory_velocity=None, reorder_risk_score=None,
            reorder_risk_band=None, last_sold_at=row[2],
        )
        for row in product_rows
    }
    return retailers, products


def _load_anomalies(con, *, limit: int = 200) -> list[AnomalyRecord]:
    try:
        rows = con.execute(
            """
            select anomaly_id, anomaly_type, severity, detected_at, entity_type, entity_id,
                   metric_name, metric_value, baseline_value, deviation
            from anomalies.anomaly_events
            order by detected_at desc
            limit ?
            """,
            [limit],
        ).fetchall()
    except Exception:
        return []
    return [
        AnomalyRecord(
            anomaly_id=r[0], anomaly_type=r[1], severity=r[2], detected_at=r[3], entity_type=r[4],
            entity_id=r[5], metric_name=r[6], metric_value=r[7], baseline_value=r[8], deviation=r[9],
        )
        for r in rows
    ]


def _load_ml_predictions(con, *, limit: int = 100) -> MLPredictionSnapshot:
    def _safe_select(sql: str) -> list[dict]:
        try:
            result = con.execute(sql, [limit])
            cols = [c[0] for c in result.description]
            return [dict(zip(cols, row)) for row in result.fetchall()]
        except Exception:
            return []

    return MLPredictionSnapshot(
        forecasts=_safe_select("select * from ml.forecasts order by generated_at desc limit ?"),
        clusters=_safe_select("select * from ml.clusters order by computed_at desc limit ?"),
        recommendations=_safe_select("select * from ml.recommendations order by generated_at desc limit ?"),
        anomaly_classifications=_safe_select(
            "select * from ml.anomaly_classifications order by classified_at desc limit ?"
        ),
    )


def load_digital_twin(tenant_id: str | None = None, db_path: Path = DUCKDB_PATH) -> DigitalTwinState:
    """Snapshots the current warehouse into a `DigitalTwinState`. See module
    docstring for the classic-vs-tenant scope split. Safe to call against an
    empty/fresh warehouse (every underlying select is wrapped to degrade to
    an empty result rather than raise, matching `multi_tenant/
    tenant_manager.py`'s `get_tenant()`/`list_tenants()` "missing table ==
    nothing created yet, not a crash" convention)."""
    with connect_with_retry(db_path, read_only=True) as con:
        if tenant_id is None:
            retailers, products = _load_classic_twin(con)
        else:
            retailers, products = _load_tenant_twin(con, tenant_id)
        anomalies = _load_anomalies(con)
        ml_predictions = _load_ml_predictions(con)

    return DigitalTwinState(
        tenant_id=tenant_id,
        as_of=_utc_now(),
        retailers=retailers,
        products=products,
        recent_anomalies=anomalies,
        ml_predictions=ml_predictions,
    )


def refresh(state: DigitalTwinState, db_path: Path = DUCKDB_PATH) -> DigitalTwinState:
    """"Update per real-time ingestion" - re-reads the warehouse for the same
    tenant scope this state was already loaded for, returning a fresh
    snapshot (does not mutate `state`, matching `clone()`'s "never mutate a
    state you don't own" posture)."""
    return load_digital_twin(state.tenant_id, db_path)


if __name__ == "__main__":
    twin = load_digital_twin()
    print(json.dumps(twin.to_summary_dict(), indent=2))
