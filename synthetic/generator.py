"""Deterministic (seeded) synthetic marketplace data generator.

Produces retailers, products, orders, and full event chains (order_created ->
order_paid -> orders_shipped, plus product-anchored inventory_updated and
price_changed events) across a configurable date range, with seasonality
(weekend order lift), anomalies (occasional bulk orders), and a controlled
fraction of deliberately-invalid records so a normal run always exercises
quarantine - mirroring what a real upstream source looks like.

This module only builds in-memory data structures (see GeneratedDataset). It
does not touch the filesystem or DuckDB - see synthetic/write_raw.py for that,
and orchestration/synthetic_flow.py for the end-to-end config -> generate ->
write -> ingest flow.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from ingestion.paths import CONTRACTS_DIR
from ingestion.validate import CONTRACT_BY_ENTITY
from synthetic.id_registry import Registry, build_registry

RETAILER_ADJ = [
    "Northstar", "Juniper", "Harbor", "Maple", "Cedar", "Amber", "Slate", "Wildwood",
    "Coastal", "Union", "Foxglove", "Lantern", "Basalt", "Meridian", "Thistle",
    "Birchwood", "Copperline", "Driftwood", "Larkspur", "Hollow",
]
RETAILER_NOUN = [
    "Mercantile", "Goods", "Supply Co.", "Trading Co.", "Collective", "Market",
    "Provisions", "Outfitters", "Emporium", "Depot", "Exchange", "Works",
    "Studio", "Home Co.", "General Store",
]
PRODUCT_ADJ = [
    "Ceramic", "Linen", "Wooden", "Brushed Steel", "Woven", "Recycled", "Matte",
    "Hand-Poured", "Organic", "Powder-Coated", "Reclaimed", "Textured",
    "Minimalist", "Cast Iron", "Bamboo",
]
PRODUCT_NOUN = [
    "Pour Over", "Market Tote", "Stacking Set", "Throw Blanket", "Desk Lamp",
    "Storage Bin", "Candle", "Cutting Board", "Water Bottle", "Plant Stand",
    "Utility Apron", "Wall Shelf", "Trivet", "Serving Bowl", "Garden Tool Set",
]


def _iso(day: date) -> str:
    return day.isoformat()


def _date_range(start: str, end: str) -> list[date]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    days = []
    current = start_date
    while current <= end_date:
        days.append(current)
        current += timedelta(days=1)
    return days


def _weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights.keys())
    values = list(weights.values())
    return rng.choices(keys, weights=values, k=1)[0]


def _random_ts(rng: random.Random, day: date, hour: int | None = None) -> datetime:
    hour = hour if hour is not None else rng.randrange(7, 22)
    minute = rng.randrange(0, 60)
    second = rng.randrange(0, 60)
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=timezone.utc)


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


_SCHEMA_CACHE: dict[str, dict] = {}


def _load_schema(entity: str) -> dict:
    if entity not in _SCHEMA_CACHE:
        path = CONTRACTS_DIR / CONTRACT_BY_ENTITY[entity]
        _SCHEMA_CACHE[entity] = json.loads(path.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE[entity]


def make_invalid_variant(rng: random.Random, entity: str, record: dict) -> dict:
    """Return a copy of `record` deliberately mutated to fail `entity`'s real
    JSONSchema contract (read from contracts/), guaranteeing it lands in
    quarantine rather than raw/valid. Mutation strategy is chosen from what the
    schema actually enforces, so this never silently drifts out of sync with
    the contracts as they evolve."""
    schema = _load_schema(entity)
    required: list[str] = schema.get("required", [])
    properties: dict = schema.get("properties", {})
    broken = dict(record)

    pattern_fields = [f for f in required if "pattern" in properties.get(f, {})]
    enum_fields = [
        f for f in required if "enum" in properties.get(f, {}) or "const" in properties.get(f, {})
    ]
    numeric_fields = [
        f for f in required if properties.get(f, {}).get("type") in ("integer", "number")
    ]

    strategies = ["missing_required"] if required else []
    if pattern_fields:
        strategies.append("bad_pattern")
    if enum_fields:
        strategies.append("bad_enum")
    if numeric_fields:
        strategies.append("wrong_type")
    if not strategies:
        return broken

    strategy = rng.choice(strategies)
    if strategy == "missing_required":
        broken.pop(rng.choice(required), None)
    elif strategy == "bad_pattern":
        broken[rng.choice(pattern_fields)] = "INVALID-FORMAT-000"
    elif strategy == "bad_enum":
        field_name = rng.choice(enum_fields)
        spec = properties.get(field_name, {})
        forbidden = set(spec.get("enum", [])) | {spec.get("const")}
        broken[field_name] = next(
            candidate
            for candidate in ["unknown_value", "UNSET", "n/a", "pending_review"]
            if candidate not in forbidden
        )
    elif strategy == "wrong_type":
        broken[rng.choice(numeric_fields)] = "not-a-number"
    return broken


@dataclass
class GeneratedDataset:
    retailers_by_day: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    products_by_day: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    orders_by_day: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    # events[event_type][date_iso][hour] -> list[dict]
    events: dict[str, dict[str, dict[int, list[dict]]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    )
    summary: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _maybe_invalid(
    rng: random.Random, cfg: dict, entity: str, record: dict, bucket: list[dict], dataset: GeneratedDataset
) -> None:
    """Appends `record` to `bucket`, and with `anomalies.invalid_record_rate`
    probability also appends a deliberately-broken sibling so quarantine gets
    exercised for this entity/day."""
    bucket.append(record)
    dataset.summary[entity] += 1
    if rng.random() < cfg["anomalies"]["invalid_record_rate"]:
        bucket.append(make_invalid_variant(rng, entity, record))
        dataset.summary[f"{entity}_invalid"] += 1


def generate_dataset(cfg: dict, registry: Registry | None = None, data_dir=None) -> GeneratedDataset:
    rng = random.Random(cfg["seed"])
    if registry is None:
        registry = build_registry(data_dir) if data_dir else build_registry()
    dataset = GeneratedDataset()

    days = _date_range(cfg["date_range"]["start"], cfg["date_range"]["end"])
    if not days:
        raise ValueError("synthetic.yaml date_range must contain at least one day")

    # ---- Retailers: spread new_count across the range -------------------------------
    retailer_cfg = cfg["retailers"]
    new_retailer_days = [rng.choice(days) for _ in range(retailer_cfg["new_count"])]
    for signup_day in new_retailer_days:
        retailer_id = registry.next_id("retailer")
        record = {
            "retailer_id": retailer_id,
            "name": f"{rng.choice(RETAILER_ADJ)} {rng.choice(RETAILER_NOUN)}",
            "country": rng.choice(retailer_cfg["countries"]),
            "category": rng.choice(retailer_cfg["categories"]),
            "signup_date": _iso(signup_day),
            "status": _weighted_choice(rng, retailer_cfg["status_weights"]),
        }
        registry.retailers[retailer_id] = record
        _maybe_invalid(rng, cfg, "retailers", record, dataset.retailers_by_day[_iso(signup_day)], dataset)

    # ---- Products: spread new_count across the range, mostly reusing known brands ----
    product_cfg = cfg["products"]
    new_product_days = [rng.choice(days) for _ in range(product_cfg["new_count"])]
    for launch_day in new_product_days:
        if registry.brand_ids and rng.random() > product_cfg["new_brand_rate"]:
            brand_id = rng.choice(registry.brand_ids)
        else:
            brand_id = registry.next_id("brand")
            registry.brand_ids.append(brand_id)
        unit_price = round(rng.uniform(*product_cfg["unit_price_range"]), 2)
        margin = rng.uniform(*product_cfg["margin_range"])
        unit_cost = round(unit_price * (1 - margin), 2)
        product_id = registry.next_id("product")
        record = {
            "product_id": product_id,
            "brand_id": brand_id,
            "name": f"{rng.choice(PRODUCT_ADJ)} {rng.choice(PRODUCT_NOUN)}",
            "category": rng.choice(product_cfg["categories"]),
            "unit_price": unit_price,
            "unit_cost": unit_cost,
            "inventory_count": rng.randrange(*product_cfg["inventory_count_range"]),
            "is_active": True,
        }
        registry.products[product_id] = record
        _maybe_invalid(rng, cfg, "products", record, dataset.products_by_day[_iso(launch_day)], dataset)

    if not registry.retailers or not registry.products:
        raise ValueError(
            "No retailer/product catalog available (existing data + new_count are both "
            "empty) - orders need at least one retailer and one product to reference."
        )

    # Running per-product inventory levels, seeded from the catalog and mutated by
    # inventory_updated events as we walk the date range in order.
    live_inventory: dict[str, int] = {
        pid: int(record.get("inventory_count", 0)) for pid, record in registry.products.items()
    }
    live_price: dict[str, float] = {
        pid: float(record.get("unit_price", 0)) for pid, record in registry.products.items()
    }

    order_cfg = cfg["orders"]
    event_cfg = cfg["events"]
    anomaly_cfg = cfg["anomalies"]

    for day in days:
        retailer_pool = list(registry.retailers.keys())
        product_pool = list(registry.products.keys())

        # ---- Orders + their event chain -----------------------------------------
        volume = rng.randint(*order_cfg["daily_volume_range"])
        if day.weekday() >= 5:  # Saturday/Sunday
            volume = round(volume * order_cfg["weekend_multiplier"])

        for _ in range(volume):
            retailer_id = rng.choice(retailer_pool)
            product_id = rng.choice(product_pool)
            unit_price = live_price.get(product_id, registry.products[product_id]["unit_price"])
            quantity = rng.randint(*order_cfg["quantity_range"])
            if rng.random() < anomaly_cfg["large_order_rate"]:
                quantity *= rng.randint(*anomaly_cfg["large_order_multiplier_range"])
            gross_amount = round(unit_price * quantity, 2)
            discount_amount = round(gross_amount * rng.uniform(*order_cfg["discount_rate_range"]), 2)

            order_ts = _random_ts(rng, day)
            order_id = registry.next_id("order")
            cancelled = rng.random() < order_cfg["cancelled_rate"]
            status = "created"

            order_record = {
                "order_id": order_id,
                "retailer_id": retailer_id,
                "product_id": product_id,
                "order_ts": _fmt_ts(order_ts),
                "quantity": quantity,
                "gross_amount": gross_amount,
                "discount_amount": discount_amount,
                "status": "created",  # finalized below once the event chain plays out
            }

            created_event = {
                "event_id": registry.next_id("event"),
                "event_type": "order_created",
                "event_ts": _fmt_ts(order_ts + timedelta(seconds=rng.randint(1, 90))),
                "order_id": order_id,
                "retailer_id": retailer_id,
                "product_id": product_id,
                "quantity": quantity,
                "gross_amount": gross_amount,
            }
            _maybe_invalid(
                rng, cfg, "order_created", created_event,
                _hour_bucket(dataset, "order_created", day, created_event), dataset,
            )

            if not cancelled and rng.random() < event_cfg["order_paid_rate"]:
                status = "paid"
                paid_ts = order_ts + timedelta(minutes=rng.randint(*event_cfg["payment_lag_minutes"]))
                paid_event = {
                    "event_id": registry.next_id("event"),
                    "event_type": "order_paid",
                    "event_ts": _fmt_ts(paid_ts),
                    "order_id": order_id,
                    "amount": round(gross_amount - discount_amount, 2),
                }
                _maybe_invalid(
                    rng, cfg, "order_paid", paid_event,
                    _hour_bucket(dataset, "order_paid", paid_ts.date(), paid_event), dataset,
                )

                if rng.random() < event_cfg["orders_shipped_rate"]:
                    status = "shipped"
                    shipped_ts = paid_ts + timedelta(
                        minutes=rng.randint(*event_cfg["shipping_lag_minutes"])
                    )
                    shipped_event = {
                        "event_id": registry.next_id("event"),
                        "event_type": "orders_shipped",
                        "event_ts": _fmt_ts(shipped_ts),
                        "order_id": order_id,
                        "carrier": rng.choice(event_cfg["carriers"]),
                    }
                    _maybe_invalid(
                        rng, cfg, "orders_shipped", shipped_event,
                        _hour_bucket(dataset, "orders_shipped", shipped_ts.date(), shipped_event),
                        dataset,
                    )

            order_record["status"] = status if not cancelled else "cancelled"
            _maybe_invalid(rng, cfg, "orders", order_record, dataset.orders_by_day[_iso(day)], dataset)

        # ---- Inventory movement ---------------------------------------------------
        for _ in range(rng.randint(*event_cfg["inventory_updates_per_day_range"])):
            product_id = rng.choice(product_pool)
            delta = rng.randint(*event_cfg["inventory_delta_range"])
            new_level = max(0, live_inventory.get(product_id, 0) + delta)
            live_inventory[product_id] = new_level
            inv_ts = _random_ts(rng, day)
            inv_event = {
                "event_id": registry.next_id("event"),
                "event_type": "inventory_updated",
                "event_ts": _fmt_ts(inv_ts),
                "product_id": product_id,
                "delta": delta,
                "inventory_count_after": new_level,
            }
            _maybe_invalid(
                rng, cfg, "inventory_updated", inv_event,
                _hour_bucket(dataset, "inventory_updated", day, inv_event), dataset,
            )

        # ---- Price changes ----------------------------------------------------------
        for _ in range(rng.randint(*event_cfg["price_changes_per_day_range"])):
            product_id = rng.choice(product_pool)
            old_price = live_price.get(product_id, registry.products[product_id]["unit_price"])
            pct = rng.uniform(*event_cfg["price_change_pct_range"])
            new_price = max(1.0, round(old_price * (1 + pct), 2))
            live_price[product_id] = new_price
            price_ts = _random_ts(rng, day)
            price_event = {
                "event_id": registry.next_id("event"),
                "event_type": "price_changed",
                "event_ts": _fmt_ts(price_ts),
                "product_id": product_id,
                "old_price": round(old_price, 2),
                "new_price": new_price,
            }
            _maybe_invalid(
                rng, cfg, "price_changed", price_event,
                _hour_bucket(dataset, "price_changed", day, price_event), dataset,
            )

    # Touch every day's bucket (even if empty) so a caller writing these out to
    # data/batch/**/YYYY/MM/DD/*.json overwrites *every* day in range - including
    # days that happened to get zero new retailers/products this run. That matters
    # when replacing pre-existing files that don't validate against the current
    # contracts (rather than silently leaving a stale/broken file untouched).
    for day in days:
        dataset.retailers_by_day[_iso(day)]
        dataset.products_by_day[_iso(day)]
        dataset.orders_by_day[_iso(day)]

    return dataset


def _hour_bucket(dataset: GeneratedDataset, event_type: str, day: date, record: dict) -> list[dict]:
    event_ts = datetime.strptime(record["event_ts"], "%Y-%m-%dT%H:%M:%SZ")
    return dataset.events[event_type][_iso(day)][event_ts.hour]


if __name__ == "__main__":
    import yaml

    from ingestion.paths import PROJECT_ROOT

    with (PROJECT_ROOT / "config" / "synthetic.yaml").open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    generated = generate_dataset(config)
    print("Generated (valid + deliberately-invalid) record counts:")
    for key, count in sorted(generated.summary.items()):
        print(f"  {key}: {count}")
