"""Tenant-scoped API routes (PHASE7-DEPLOYMENT.md Section 2/4/8).

Mounted into api/metrics_api.py alongside auth_api.py's router. The one
gap auth_api.py/auth_middleware.py left open: those modules built
sign-up/login/RBAC and a `require_tenant()` dependency, but nothing yet
exposed the tenant-scoped data those tokens are meant to unlock - this
router is that surface, and it's the first genuinely auth-gated part of
this API (every other Phase 3-6 route stays open by design - see
auth/auth_middleware.py's module docstring).

Deliberately narrow: `list_tenants`/`get_tenant` come straight from
multi_tenant/tenant_manager.py, and the three per-tenant metric endpoints
read the exact tables warehouse/duckdb/tenant_elt.sql and
compute/polars/tenant_metrics.py populate
(marts.metrics_tenant_daily / marts.compute_tenant_health /
marts.compute_tenant_growth) - no new aggregation logic here, same
"expose what's already computed" posture as
observability/metrics.py's refresh_from_warehouse(). Since "orders" is the
only entity carried end-to-end through the tenant-aware pipeline (see
ingestion/tenant_ingest.py's module docstring for that scope call), these
are the only tenant-scoped metrics that exist to expose - there is
deliberately no tenant-scoped /retailers or /products endpoint here, since
no tenant-scoped retailer/product table exists yet.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.db import query_safe
from auth.auth_middleware import AuthenticatedUser, require_tenant
from multi_tenant.tenant_manager import get_tenant, list_tenants

router = APIRouter(prefix="/tenants", tags=["tenants"])


def _tenant_to_dict(tenant) -> dict:
    return {
        "tenant_id": tenant.tenant_id,
        "name": tenant.name,
        "status": tenant.status,
        "isolation_policy": tenant.isolation_policy,
        "created_at": tenant.created_at,
    }


@router.get("")
def tenants_index(current_user: AuthenticatedUser = Depends(require_tenant())) -> list[dict]:
    """Every role can call this (not gated to admin) but a non-admin only
    ever sees their own tenant - same one-tenant-at-a-time posture
    `require_tenant()` enforces everywhere else, so a tenant_admin/analyst/
    viewer's "switch tenant" UI (frontend/lib/tenant.ts) has something
    non-empty to render without needing platform-admin rights just to see
    their own workspace's name."""
    if current_user.role == "admin":
        return [_tenant_to_dict(t) for t in list_tenants()]
    tenant = get_tenant(current_user.tenant_id)
    return [_tenant_to_dict(tenant)] if tenant else []


@router.get("/{tenant_id}")
def tenant_detail(tenant_id: str, current_user: AuthenticatedUser = Depends(require_tenant())) -> dict:
    tenant = get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return _tenant_to_dict(tenant)


@router.get("/{tenant_id}/daily")
def tenant_daily(tenant_id: str, current_user: AuthenticatedUser = Depends(require_tenant())) -> list[dict]:
    return query_safe(
        "select * from marts.metrics_tenant_daily where tenant_id = ? order by order_date", [tenant_id]
    )


@router.get("/{tenant_id}/health")
def tenant_health(tenant_id: str, current_user: AuthenticatedUser = Depends(require_tenant())) -> dict | None:
    rows = query_safe("select * from marts.compute_tenant_health where tenant_id = ?", [tenant_id])
    return rows[0] if rows else None


@router.get("/{tenant_id}/growth")
def tenant_growth(tenant_id: str, current_user: AuthenticatedUser = Depends(require_tenant())) -> dict | None:
    rows = query_safe("select * from marts.compute_tenant_growth where tenant_id = ?", [tenant_id])
    return rows[0] if rows else None
