"""Feature Engineering Layer (PHASE6-ML.md Section 2).

A unified feature store: every builder below produces `FeatureRow`s with the
same shape - `(entity_type, entity_id, feature_group, features: dict)` - and
they all land in one table, `ml.features`, rather than one bespoke table per
group. This mirrors anomalies/detector.py's `metadata` and monitoring/
metrics.py's `metric_value`+`metadata` pattern: a flexible JSON payload
column (`features`) keeps the table schema stable as feature sets evolve,
while `(entity_type, entity_id, feature_group)` keeps rows queryable and
joinable without needing a wide table with one column per feature that grows
every time a builder adds a metric.

Four builders, one per PHASE6-ML.md Section 2 group:

- `build_retailer_features`: daily GMV, velocity, event lag, anomaly counts,
  inventory volatility, price volatility, health score. Inventory/price
  volatility have no direct retailer-level meaning in this schema (retailers
  don't own inventory or set prices - products do), so they're computed over
  the products each retailer actually ordered in the lookback window: a
  retailer whose ordered products have swung wildly in stock or price gets a
  high volatility feature, which is a reasonable proxy for "this retailer's
  supply is unstable" even though the number isn't literally the retailer's
  own.
- `build_product_features`: velocity, reorder risk (from Phase 4's
  compute_product_reorder_risk), inventory volatility, a price-elasticity
  proxy, anomaly counts. True price elasticity needs a controlled
  price/demand experiment this demo doesn't have; the proxy used here is the
  Pearson correlation between order quantity and the implied per-unit price
  (`gross_amount / quantity`) across a product's own orders in the window -
  negative values behave like real elasticity (higher price, lower
  quantity), positive/near-zero values mean price isn't visibly moving
  demand for that product. Documented as a proxy, not textbook elasticity.
- `build_order_features`: lifecycle duration, payment lag, shipping lag, one
  row per order - pivoting `marts.fact_orders_events`' three lifecycle event
  types (order_created/order_paid/orders_shipped) onto one row per order_id.
  Orders missing a later stage (never paid, never shipped) get a null lag
  for that stage rather than being dropped, so an incomplete order is still
  visible as a feature row instead of silently disappearing.
- `build_event_features`: system-level (not per-entity) event frequency and
  event-type distribution over the window - entity_type='system',
  entity_id='marketplace', same convention anomalies/detector.py uses for
  its system-level GMV/order-velocity/ingestion-volume detectors.

`build_all_features()` is the orchestration entry point: runs all four,
persists them, and emits one `ml_feature_built` lineage edge per feature
group (not per row - that would be thousands of edges for order features on
any real dataset) from the source tables that group reads to `ml.features`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DUCKDB_PATH
from ml.config import load_ml_config

# Order features are per-order (not per-entity-aggregate like the other three
# groups), so on a busy dataset this could be large. Capped and logged, same
# "no silent caps" philosophy as ml/models/forecasting.py's top_n_entities -
# see build_order_features()'s docstring.
MAX_ORDER_FEATURE_ROWS = 2000


@dataclass(frozen=True)
class FeatureRow:
    feature_id: str
    entity_type: str
    entity_id: str
    feature_group: str  # retailer | product | order | event
    computed_at: str
    window_start: str | None
    window_end: str | None
    features: dict[str, Any]


def _feature_id(feature_group: str, entity_id: str) -> str:
    compact = utc_now().replace(":", "").replace("-", "").replace(".", "").replace("+", "z")
    safe_entity = "".join(ch if ch.isalnum() else "_" for ch in str(entity_id))[:40]
    return f"feat_{feature_group}_{safe_entity}_{compact}_{uuid.uuid4().hex[:8]}"


def _row(feature_group: str, entity_type: str, entity_id: str, features: dict[str, Any], *, window_start: str, window_end: str) -> FeatureRow:
    # None values are dropped (not persisted as null-in-JSON) - a feature
    # that couldn't be computed for this entity (e.g. no price_changed
    # events for a product in the window) should be *absent*, not present
    # with a misleading null, since every consumer (ml/models/clustering.py,
    # forecasting.py) has to decide how to treat missing-vs-null anyway.
    clean = {k: v for k, v in features.items() if v is not None}
    return FeatureRow(
        feature_id=_feature_id(feature_group, entity_id),
        entity_type=entity_type,
        entity_id=str(entity_id),
        feature_group=feature_group,
        computed_at=utc_now(),
        window_start=window_start,
        window_end=window_end,
        features=clean,
    )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_retailer_features(con, *, lookback_days: int = 30) -> list[FeatureRow]:
    window_end = utc_now()
    rows = con.execute(
        f"""
        select
          d.retailer_id,
          coalesce(g.total_gmv / nullif(g.active_days, 0), 0) as daily_gmv,
          coalesce(g.total_orders / nullif(g.active_days, 0), 0) as velocity,
          l.avg_event_lag_seconds,
          coalesce(ac.anomaly_count, 0) as anomaly_count,
          inv.inventory_volatility,
          pr.price_volatility,
          h.retailer_health_score
        from marts.dim_retailer d
        left join (
          select retailer_id, sum(gmv) as total_gmv, sum(order_count) as total_orders,
                 count(distinct order_date) as active_days
          from marts.metrics_retailer_daily
          where order_date >= current_date - interval '{lookback_days} days'
          group by retailer_id
        ) g on d.retailer_id = g.retailer_id
        left join (
          select retailer_id, avg(date_diff('second', event_ts, loaded_at)) as avg_event_lag_seconds
          from marts.fact_orders_events
          where event_ts >= current_timestamp - interval '{lookback_days} days'
          group by retailer_id
        ) l on d.retailer_id = l.retailer_id
        left join (
          select entity_id as retailer_id, count(*) as anomaly_count
          from anomalies.anomaly_events
          where entity_type = 'retailer' and detected_at >= current_timestamp - interval '{lookback_days} days'
          group by entity_id
        ) ac on d.retailer_id = ac.retailer_id
        left join (
          select rp.retailer_id, stddev_pop(pe.delta) as inventory_volatility
          from (
            select distinct retailer_id, product_id from marts.fact_orders
            where order_ts >= current_timestamp - interval '{lookback_days} days'
          ) rp
          join marts.fact_product_events pe on rp.product_id = pe.product_id
          where pe.event_type = 'inventory_updated'
            and pe.event_ts >= current_timestamp - interval '{lookback_days} days'
          group by rp.retailer_id
        ) inv on d.retailer_id = inv.retailer_id
        left join (
          select rp.retailer_id,
                 stddev_pop((pe.new_price - pe.old_price) / nullif(pe.old_price, 0)) as price_volatility
          from (
            select distinct retailer_id, product_id from marts.fact_orders
            where order_ts >= current_timestamp - interval '{lookback_days} days'
          ) rp
          join marts.fact_product_events pe on rp.product_id = pe.product_id
          where pe.event_type = 'price_changed'
            and pe.event_ts >= current_timestamp - interval '{lookback_days} days'
            and pe.old_price > 0
          group by rp.retailer_id
        ) pr on d.retailer_id = pr.retailer_id
        left join marts.compute_retailer_health h on d.retailer_id = h.retailer_id
        """
    ).fetchall()
    columns = ["retailer_id", "daily_gmv", "velocity", "avg_event_lag_seconds", "anomaly_count",
               "inventory_volatility", "price_volatility", "retailer_health_score"]
    out: list[FeatureRow] = []
    for values in rows:
        record = dict(zip(columns, values))
        retailer_id = record.pop("retailer_id")
        out.append(_row("retailer", "retailer", retailer_id, record, window_start=None, window_end=window_end))
    return out


def build_product_features(con, *, lookback_days: int = 30) -> list[FeatureRow]:
    window_end = utc_now()
    rows = con.execute(
        f"""
        select
          d.product_id,
          v.units_sold,
          v.order_count,
          r.reorder_risk_score,
          r.sell_through_rate,
          invvol.inventory_volatility,
          elastic.price_elasticity_proxy,
          coalesce(ac.anomaly_count, 0) as anomaly_count
        from marts.dim_product d
        left join marts.metrics_product_velocity v on d.product_id = v.product_id
        left join marts.compute_product_reorder_risk r on d.product_id = r.product_id
        left join (
          select product_id, stddev_pop(delta) as inventory_volatility
          from marts.fact_product_events
          where event_type = 'inventory_updated'
            and event_ts >= current_timestamp - interval '{lookback_days} days'
          group by product_id
        ) invvol on d.product_id = invvol.product_id
        left join (
          select product_id, corr(quantity, gross_amount / nullif(quantity, 0)) as price_elasticity_proxy
          from marts.fact_orders
          where order_ts >= current_timestamp - interval '{lookback_days} days' and quantity > 0
          group by product_id
        ) elastic on d.product_id = elastic.product_id
        left join (
          select entity_id as product_id, count(*) as anomaly_count
          from anomalies.anomaly_events
          where entity_type = 'product' and detected_at >= current_timestamp - interval '{lookback_days} days'
          group by entity_id
        ) ac on d.product_id = ac.product_id
        where d.is_active
        """
    ).fetchall()
    columns = ["product_id", "units_sold", "order_count", "reorder_risk_score", "sell_through_rate",
               "inventory_volatility", "price_elasticity_proxy", "anomaly_count"]
    out: list[FeatureRow] = []
    for values in rows:
        record = dict(zip(columns, values))
        product_id = record.pop("product_id")
        out.append(_row("product", "product", product_id, record, window_start=None, window_end=window_end))
    return out


def build_order_features(con, *, lookback_days: int = 30, max_rows: int = MAX_ORDER_FEATURE_ROWS) -> list[FeatureRow]:
    """One row per order that has at least reached order_created within the
    window. Orders that haven't been paid/shipped yet still get a row, with
    payment_lag_seconds/shipping_lag_seconds left absent (not zero - an
    order paid instantly and an order not yet paid are very different
    things, see _row()'s None-dropping behavior).

    Capped at `max_rows` most-recent orders (see MAX_ORDER_FEATURE_ROWS) - a
    genuinely large order history would make this the single most expensive
    query in the pipeline for no benefit downstream (nothing here trains a
    per-order model at the count this could reach), so it's an explicit,
    logged cap rather than an unbounded pivot."""
    window_end = utc_now()
    total = con.execute(
        f"""
        select count(distinct order_id) from marts.fact_orders_events
        where event_ts >= current_timestamp - interval '{lookback_days} days'
        """
    ).fetchone()[0]
    if total and total > max_rows:
        print(f"  build_order_features: {total} orders in window, capping to the {max_rows} most recent")

    rows = con.execute(
        f"""
        with pivoted as (
          select
            order_id,
            max(case when event_type = 'order_created' then event_ts end) as created_ts,
            max(case when event_type = 'order_paid' then event_ts end) as paid_ts,
            max(case when event_type = 'orders_shipped' then event_ts end) as shipped_ts
          from marts.fact_orders_events
          where event_ts >= current_timestamp - interval '{lookback_days} days'
          group by order_id
        )
        select
          order_id,
          date_diff('second', created_ts, shipped_ts) as lifecycle_duration_seconds,
          date_diff('second', created_ts, paid_ts) as payment_lag_seconds,
          date_diff('second', paid_ts, shipped_ts) as shipping_lag_seconds
        from pivoted
        where created_ts is not null
        order by created_ts desc
        limit {max_rows}
        """
    ).fetchall()
    columns = ["order_id", "lifecycle_duration_seconds", "payment_lag_seconds", "shipping_lag_seconds"]
    out: list[FeatureRow] = []
    for values in rows:
        record = dict(zip(columns, values))
        order_id = record.pop("order_id")
        out.append(_row("order", "order", order_id, record, window_start=None, window_end=window_end))
    return out


def build_event_features(con, *, lookback_days: int = 30) -> list[FeatureRow]:
    """System-level, not per-entity: total event frequency and the
    proportional distribution across event types over the window."""
    window_end = utc_now()
    rows = con.execute(
        f"""
        select event_type, count(*) as event_count
        from marts.fact_orders_events
        where event_ts >= current_timestamp - interval '{lookback_days} days'
        group by event_type
        union all
        select event_type, count(*) as event_count
        from marts.fact_product_events
        where event_ts >= current_timestamp - interval '{lookback_days} days'
        group by event_type
        """
    ).fetchall()
    if not rows:
        return []
    counts = {event_type: int(count) for event_type, count in rows}
    total = sum(counts.values())
    features: dict[str, Any] = {"event_frequency": total}
    for event_type, count in counts.items():
        features[f"event_share_{event_type}"] = count / total if total else 0.0
    return [_row("event", "system", "marketplace", features, window_start=None, window_end=window_end)]


# ---------------------------------------------------------------------------
# Persistence + orchestration entry point
# ---------------------------------------------------------------------------


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists ml")
    con.execute(
        """
        create table if not exists ml.features (
          feature_id varchar primary key,
          entity_type varchar,
          entity_id varchar,
          feature_group varchar,
          computed_at timestamptz,
          window_start timestamptz,
          window_end timestamptz,
          features varchar
        )
        """
    )


def persist_features(rows: list[FeatureRow], db_path: Path = DUCKDB_PATH) -> None:
    if not rows:
        return
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        con.executemany(
            """
            insert or replace into ml.features
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.feature_id, r.entity_type, r.entity_id, r.feature_group,
                    r.computed_at, r.window_start, r.window_end,
                    json.dumps(r.features, default=str, sort_keys=True),
                )
                for r in rows
            ],
        )


_SOURCE_TABLES = {
    "retailer": "marts.metrics_retailer_daily,marts.fact_orders_events,marts.fact_product_events,"
                "marts.compute_retailer_health,anomalies.anomaly_events",
    "product": "marts.dim_product,marts.metrics_product_velocity,marts.compute_product_reorder_risk,"
               "marts.fact_product_events,marts.fact_orders,anomalies.anomaly_events",
    "order": "marts.fact_orders_events",
    "event": "marts.fact_orders_events,marts.fact_product_events",
}


def build_all_features(db_path: Path = DUCKDB_PATH, *, lookback_days: int | None = None) -> list[FeatureRow]:
    if not db_path.exists():
        return []
    config = load_ml_config()
    lookback = lookback_days if lookback_days is not None else config.lookback_days

    groups: dict[str, list[FeatureRow]] = {}
    with connect_with_retry(db_path, read_only=True) as con:
        for group, builder in (
            ("retailer", build_retailer_features),
            ("product", build_product_features),
            ("order", build_order_features),
            ("event", build_event_features),
        ):
            try:
                groups[group] = builder(con, lookback_days=lookback)
            except Exception as exc:  # noqa: BLE001 - one feature group failing shouldn't block the rest
                print(f"  feature builder '{group}' failed: {exc!r}")
                groups[group] = []

    all_rows = [row for rows in groups.values() for row in rows]
    persist_features(all_rows, db_path)

    now = utc_now()
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=f"ml_features_{group}_{now}",
                source_node=_SOURCE_TABLES.get(group, "marts.*"),
                target_node="ml.features",
                edge_type="ml_feature_built",
                entity=group,
                created_at=now,
            )
            for group, rows in groups.items()
            if rows
        ],
        db_path,
    )
    return all_rows


if __name__ == "__main__":
    results = build_all_features()
    print(f"Built {len(results)} feature rows")
    for group in ("retailer", "product", "order", "event"):
        count = sum(1 for r in results if r.feature_group == group)
        print(f"  {group}: {count}")
