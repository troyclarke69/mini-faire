"""Seed synthetic order data for one tenant, then rebuild the tenant
warehouse (PHASE7-DEPLOYMENT.md Section 2/4).

Why this script exists: `ingestion/tenant_ingest.py`'s tenant pipeline is
real and complete (validate -> tag -> write -> raw.raw_tenant_orders ->
`warehouse/duckdb/tenant_elt.sql`), but it has nothing to ingest until a
tenant actually uploads order data - a real deployment's onboarding wizard
would collect that from the tenant, and `ingestion/tenant_ingest.py`'s
`ingest_tenant_directory()` is exactly the function it would call. This repo
doesn't ship that upload UI or real customer data (out of scope, same as
every other "documented gap, not silently claimed as done" spot in this
phase), so a brand-new tenant's `/tenants` dashboard has nothing to show
straight after signup - not a bug, just nothing ingested yet. This script is
the demo/dev stand-in: generates a batch of synthetic orders that satisfy
`contracts/order.schema.json`, runs them through the *same*
validate -> tag -> write path a real upload would use (no shortcut around
`ingest_tenant_directory()`), then rebuilds `raw.raw_tenant_orders` and
reruns `tenant_elt.sql` so `marts.metrics_tenant_daily` /
`compute_tenant_health` / `compute_tenant_growth` have rows.

Usage (from the project root, same venv as scripts/run_demo.py):
    python scripts/seed_tenant_orders.py <tenant_id> [--count 200] [--days 30]

`<tenant_id>` must already exist (created via signup, or
`multi_tenant.tenant_manager.create_tenant()`) - find it via the tenant
switcher in the app, or `select tenant_id from tenant.tenants`.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# scripts/ isn't a package, and running `python scripts\<name>.py` only puts
# scripts/ itself on sys.path[0] - not the project root, where ingestion/,
# multi_tenant/, etc. actually live (see ingestion/paths.py's own
# PROJECT_ROOT). That normally comes from this project's editable install
# (`pip install -e ".[dev]"`), which can go stale after a new top-level
# package is added post-install; inserting the project root here makes this
# script work regardless of whether that install is current.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compute.polars.tenant_metrics import persist_tenant_metrics  # noqa: E402
from ingestion.tenant_ingest import ingest_tenant_directory, rebuild_tenant_warehouse  # noqa: E402
from multi_tenant.tenant_manager import TenantError, get_tenant, tenant_storage_path  # noqa: E402

# Matches contracts/order.schema.json's "status" enum and rough real-world
# mix (most orders end up shipped; cancellations are rare) - not meant to be
# statistically rigorous, just plausible-looking demo data.
STATUSES = ["created", "paid", "shipped", "cancelled"]
STATUS_WEIGHTS = [0.15, 0.25, 0.55, 0.05]

# contracts/order.schema.json requires order_id/retailer_id/product_id to
# match ^ord_[0-9]+$ / ^ret_[0-9]+$ / ^prd_[0-9]+$ - a small fixed pool of
# retailer/product IDs (rather than one-off IDs per order) so
# marts.metrics_tenant_daily's per-tenant rollups have repeat retailers/
# products to aggregate over, matching how synthetic/generator.py's
# single-tenant data reuses a retailer/product pool across many orders.
RETAILER_POOL = 12
PRODUCT_POOL = 40


def _random_order(rng: random.Random, order_index: int, window_start: datetime) -> dict:
    order_ts = window_start + timedelta(
        days=rng.randrange(0, 30), hours=rng.randrange(7, 22), minutes=rng.randrange(0, 60)
    )
    gross_amount = round(rng.uniform(15, 400), 2)
    discount_amount = round(gross_amount * rng.choice([0, 0, 0, 0.1, 0.15]), 2)
    return {
        "order_id": f"ord_{order_index:06d}",
        "retailer_id": f"ret_{rng.randrange(1, RETAILER_POOL + 1):04d}",
        "product_id": f"prd_{rng.randrange(1, PRODUCT_POOL + 1):04d}",
        "order_ts": order_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "quantity": rng.randrange(1, 8),
        "gross_amount": gross_amount,
        "discount_amount": discount_amount,
        "status": rng.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0],
    }


def generate_orders(count: int, days: int, *, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    window_start = datetime.now(timezone.utc) - timedelta(days=days)
    return [_random_order(rng, i, window_start) for i in range(1, count + 1)]


def write_seed_file(tenant_id: str, orders: list[dict]) -> Path:
    """Drops a JSON array file under this tenant's `orders/inbox` -
    `ingest_tenant_directory()`'s default scan root - the same place a real
    upload would land the file. Does NOT inject tenant_id into the records
    here; `ingestion/tenant_ingest.py`'s `ingest_tenant_file()` tags that in
    *after* schema validation (contracts/order.schema.json's
    `additionalProperties: false` would reject a pre-tagged record)."""
    inbox = tenant_storage_path(tenant_id, "orders", "inbox")
    inbox.mkdir(parents=True, exist_ok=True)
    out_path = inbox / f"seed_{uuid.uuid4().hex[:8]}.json"
    out_path.write_text(json.dumps(orders, indent=2), encoding="utf-8")
    return out_path


def seed_tenant_orders(tenant_id: str, *, count: int, days: int) -> None:
    tenant = get_tenant(tenant_id)
    if tenant is None:
        raise TenantError(
            f"tenant {tenant_id!r} not found - double check the tenant_id (the tenant "
            "switcher in the app shows it, or query `select tenant_id from tenant.tenants`)"
        )

    orders = generate_orders(count, days)
    seed_path = write_seed_file(tenant_id, orders)
    print(f"Wrote {len(orders)} synthetic orders to {seed_path}")

    runs = ingest_tenant_directory(tenant_id, "orders")
    for run in runs:
        print(f"Ingested {run.file_name}: {run.valid_count} valid, {run.invalid_count} quarantined")

    counts = rebuild_tenant_warehouse()
    print(f"Rebuilt tenant warehouse (raw.raw_tenant_*): {counts}")

    # rebuild_tenant_warehouse() only takes raw files through
    # warehouse/duckdb/tenant_elt.sql (marts.fact_tenant_orders /
    # marts.metrics_tenant_daily) - it deliberately does NOT also run
    # compute/polars/tenant_metrics.py's persist_tenant_metrics(), same
    # ingestion/ELT-vs-compute separation scripts/run_demo.py already has for
    # the single-tenant pipeline (rebuild_warehouse() then, separately,
    # persist_compute_metrics()). Without this, marts.compute_tenant_health /
    # marts.compute_tenant_growth stay empty even after a successful ingest,
    # which is exactly why the /tenants page's "Lifetime GMV"/"Net revenue"/
    # "Tenant health score" KPI cards (api/tenant_api.py's /health, /growth -
    # see that router's docstring) showed $0 the first time this script ran
    # without this call, even though the per-day table below them (backed by
    # marts.metrics_tenant_daily directly) already had real numbers.
    persist_tenant_metrics()
    print("Persisted tenant compute metrics (marts.compute_tenant_health / marts.compute_tenant_growth).")
    print(f"Done - {tenant.name!r} ({tenant_id}) should now show data on /tenants.")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tenant_id", help="An existing tenant's tenant_id (see module docstring for how to find it).")
    parser.add_argument("--count", type=int, default=200, help="Number of synthetic orders to generate (default: 200).")
    parser.add_argument("--days", type=int, default=30, help="Spread orders across the last N days (default: 30).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    seed_tenant_orders(args.tenant_id, count=args.count, days=args.days)


if __name__ == "__main__":
    main()
