"""Multi-Tenant Isolation Layer (PHASE7-DEPLOYMENT.md Section 2).

Foundational module for Phase 7 - every other tenant-aware piece
(`ingestion/tenant_ingest.py`, `warehouse/duckdb/tenant_elt.sql`,
`compute/polars/tenant_metrics.py`, `ml/tenant_models/`, `auth/*`,
`storage/cloud_storage.py`) keys off the conventions this module
establishes: what a tenant ID looks like, where a tenant's files live, and
how a tenant's rows are isolated from every other tenant's inside the
shared DuckDB warehouse.

Isolation policy - the spec's "manage tenant database schemas" reconciled
with this repo's existing warehouse (~20 tables/views already built across
Phases 3-6, none of them tenant-aware): rewriting every `warehouse/duckdb/
models/*.sql` file into a per-tenant-schema template would be a much larger
change than this phase's scope, and most SaaS platforms don't actually do
that for every tenant either. Instead this module supports two isolation
policies per tenant, a real (if simplified) version of the "pooled vs silo"
split production multi-tenant platforms use:

- `pooled` (default, `ISOLATION_POOLED`): the tenant's rows live inside the
  normal shared `staging.*`/`marts.*`/`ml.*` tables, distinguished by a
  `tenant_id` column - see `warehouse/duckdb/tenant_elt.sql`. Cheap to
  provision (no DDL), the right default for most tenants, and how
  `ingestion/tenant_ingest.py` and `compute/polars/tenant_metrics.py`
  operate.
- `silo` (`ISOLATION_SILO`): the tenant gets its own DuckDB schema
  (`create_tenant_schema()` issues `create schema if not exists
  tenant_<id>`) for stronger physical isolation - e.g. a compliance-sensitive
  tenant who cannot share physical storage with other tenants' rows even
  behind a `tenant_id` filter. This phase implements schema
  creation/teardown for that isolation tier honestly, but does NOT mirror
  every mart/staging table into each silo schema (that would mean either
  templating the entire SQL layer per tenant or building a second full ELT
  path) - a silo tenant's own copies of `marts.*` are a follow-on extension,
  not claimed as done here. `isolation_policy` is recorded per tenant so
  that gap is visible in the tenant record itself, not silently assumed.

Every tenant also gets a storage_prefix (`tenants/<tenant_id>`, relative to
`ingestion/paths.py`'s `RAW_DIR`/`TENANTS_RAW_DIR` locally and to
`storage/cloud_storage.py`'s configured bucket in the cloud) - this exists
for both isolation policies, since raw file storage is prefix-isolated
regardless of how the warehouse tables isolate rows.

Tenant deletion is soft by default (`status` moves to `STATUS_DELETED`, the
row and its storage stay in place) - the same "never silently drop data"
posture this repo already takes with quarantine records and every
historical `ml.model_registry`/`anomalies.anomaly_events` row. `hard=True`
additionally drops a silo tenant's schema, but never deletes raw files -
that stays an explicit operator action against `storage/cloud_storage.py`,
consistent with how e.g. `data/ml_models/*.pkl` artifacts are never
auto-deleted by `ml/registry.py` either.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import LineageEdge, upsert_lineage_edges, utc_now
from ingestion.paths import DUCKDB_PATH, TENANTS_RAW_DIR

STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"  # temporarily denied access (e.g. billing hold) - data and schema untouched
STATUS_DELETED = "deleted"  # soft-deleted - row and storage retained for audit/recovery

ISOLATION_POOLED = "pooled"
ISOLATION_SILO = "silo"
ISOLATION_POLICIES = (ISOLATION_POOLED, ISOLATION_SILO)

# Lowercase, digits, underscore, hyphen; 3-40 chars; must start/end alnum.
# Doubles as a DuckDB-identifier-safe and URL/path-safe token, since
# tenant_id is used directly in schema names (tenant_<id>) and storage
# prefixes (tenants/<id>/...).
_TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,38}[a-z0-9]$")


class TenantError(ValueError):
    """Raised for invalid tenant IDs, duplicate tenants, or operations
    against a tenant that doesn't exist / isn't active - a distinct type so
    callers (auth_middleware.py in particular) can catch it specifically
    rather than swallowing arbitrary ValueErrors."""


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    name: str
    status: str
    isolation_policy: str
    storage_prefix: str
    schema_name: str | None  # set only when isolation_policy == ISOLATION_SILO
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


def _row_to_tenant(row: tuple) -> Tenant:
    (tenant_id, name, status, isolation_policy, storage_prefix, schema_name,
     metadata, created_at, updated_at) = row
    return Tenant(
        tenant_id=tenant_id,
        name=name,
        status=status,
        isolation_policy=isolation_policy,
        storage_prefix=storage_prefix,
        schema_name=schema_name,
        metadata=json.loads(metadata) if metadata else {},
        created_at=str(created_at),
        updated_at=str(updated_at),
    )


def validate_tenant_id(tenant_id: str) -> str:
    """Raises TenantError if `tenant_id` isn't safe to use as a DuckDB
    schema-name fragment and a storage path segment; returns it unchanged
    otherwise, so this doubles as an inline validation call."""
    if not isinstance(tenant_id, str) or not _TENANT_ID_PATTERN.match(tenant_id):
        raise TenantError(
            f"invalid tenant_id {tenant_id!r}: must be 3-40 chars, lowercase "
            "letters/digits/underscore/hyphen, starting and ending with a letter or digit"
        )
    return tenant_id


def generate_tenant_id(name: str) -> str:
    """Slugifies `name` and appends a short random suffix for uniqueness -
    used when a caller (e.g. the onboarding wizard - see
    frontend/app/onboarding, auth/auth_api.py's signup flow when a brand-new
    organization signs up) wants a tenant_id assigned rather than chosen."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:24] or "tenant"
    suffix = uuid.uuid4().hex[:8]
    return validate_tenant_id(f"{slug}-{suffix}")


def _ensure_tables(con) -> None:
    con.execute("create schema if not exists tenant")
    con.execute(
        """
        create table if not exists tenant.tenants (
          tenant_id varchar primary key,
          name varchar,
          status varchar,
          isolation_policy varchar,
          storage_prefix varchar,
          schema_name varchar,
          metadata varchar,
          created_at timestamptz,
          updated_at timestamptz
        )
        """
    )


def tenant_storage_path(tenant_id: str, *parts: str) -> Path:
    """Local filesystem path for a tenant's raw zone - the on-disk mirror of
    the storage_prefix a Tenant record carries, used by
    ingestion/tenant_ingest.py. `storage/cloud_storage.py` uses the same
    `tenants/<tenant_id>/...` prefix shape against whichever cloud backend
    is configured, so a tenant's data lands at the same relative layout
    locally or in the cloud."""
    validate_tenant_id(tenant_id)
    return TENANTS_RAW_DIR.joinpath(tenant_id, *parts)


def create_tenant(
    tenant_id: str,
    name: str,
    *,
    isolation_policy: str = ISOLATION_POOLED,
    metadata: dict[str, Any] | None = None,
    db_path: Path = DUCKDB_PATH,
) -> Tenant:
    validate_tenant_id(tenant_id)
    if isolation_policy not in ISOLATION_POLICIES:
        raise TenantError(f"isolation_policy must be one of {ISOLATION_POLICIES}, got {isolation_policy!r}")

    storage_prefix = f"tenants/{tenant_id}"
    schema_name = f"tenant_{tenant_id.replace('-', '_')}" if isolation_policy == ISOLATION_SILO else None
    now = utc_now()

    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        existing = con.execute("select tenant_id from tenant.tenants where tenant_id = ?", [tenant_id]).fetchone()
        if existing is not None:
            raise TenantError(f"tenant {tenant_id!r} already exists")
        con.execute(
            "insert into tenant.tenants values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                tenant_id, name, STATUS_ACTIVE, isolation_policy, storage_prefix, schema_name,
                json.dumps(metadata or {}, default=str, sort_keys=True), now, now,
            ],
        )
        if schema_name is not None:
            # DuckDB schema names can't be parameterized - safe here only
            # because schema_name is derived from a tenant_id that already
            # passed validate_tenant_id()'s character allowlist above.
            con.execute(f"create schema if not exists {schema_name}")

    tenant_storage_path(tenant_id).mkdir(parents=True, exist_ok=True)
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=f"tenant_created_{tenant_id}",
                source_node="multi_tenant://tenant_manager",
                target_node="tenant.tenants",
                edge_type="tenant_created",
                entity=tenant_id,
                created_at=now,
            )
        ],
        db_path,
    )
    return Tenant(
        tenant_id=tenant_id, name=name, status=STATUS_ACTIVE, isolation_policy=isolation_policy,
        storage_prefix=storage_prefix, schema_name=schema_name, metadata=metadata or {},
        created_at=now, updated_at=now,
    )


def get_tenant(tenant_id: str, db_path: Path = DUCKDB_PATH) -> Tenant | None:
    with connect_with_retry(db_path, read_only=True) as con:
        # No _ensure_tables() on this read-only connection - CREATE is
        # refused against a read-only-attached DuckDB database even when
        # the object already exists (see ml/registry.py's get_active_model()
        # for the incident this pattern fixed in Phase 6). tenant.tenants is
        # only ever written by the write-connection functions in this
        # module, so a missing table here means no tenant has ever been
        # created yet - report that as "tenant not found", not a crash.
        try:
            row = con.execute(
                "select tenant_id, name, status, isolation_policy, storage_prefix, schema_name, "
                "metadata, created_at, updated_at from tenant.tenants where tenant_id = ?",
                [tenant_id],
            ).fetchone()
        except Exception:
            return None
    return _row_to_tenant(row) if row else None


def list_tenants(*, status: str | None = None, db_path: Path = DUCKDB_PATH) -> list[Tenant]:
    with connect_with_retry(db_path, read_only=True) as con:
        try:
            if status is not None:
                rows = con.execute(
                    "select tenant_id, name, status, isolation_policy, storage_prefix, schema_name, "
                    "metadata, created_at, updated_at from tenant.tenants where status = ? order by created_at",
                    [status],
                ).fetchall()
            else:
                rows = con.execute(
                    "select tenant_id, name, status, isolation_policy, storage_prefix, schema_name, "
                    "metadata, created_at, updated_at from tenant.tenants order by created_at"
                ).fetchall()
        except Exception:
            return []
    return [_row_to_tenant(row) for row in rows]


def update_tenant_metadata(
    tenant_id: str, metadata_patch: dict[str, Any], db_path: Path = DUCKDB_PATH
) -> Tenant | None:
    """Merges `metadata_patch` into the tenant's existing metadata dict
    (shallow merge - a key set to None in the patch removes it), matching
    ml/registry.py's TaskUpdate-style "merge, don't replace wholesale"
    convention used elsewhere in this repo's tool surface."""
    now = utc_now()
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        row = con.execute("select metadata from tenant.tenants where tenant_id = ?", [tenant_id]).fetchone()
        if row is None:
            return None
        current = json.loads(row[0]) if row[0] else {}
        for key, value in metadata_patch.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        con.execute(
            "update tenant.tenants set metadata = ?, updated_at = ? where tenant_id = ?",
            [json.dumps(current, default=str, sort_keys=True), now, tenant_id],
        )
    return get_tenant(tenant_id, db_path)


def set_tenant_status(tenant_id: str, status: str, db_path: Path = DUCKDB_PATH) -> Tenant | None:
    if status not in (STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_DELETED):
        raise TenantError(f"unknown status {status!r}")
    now = utc_now()
    with connect_with_retry(db_path) as con:
        _ensure_tables(con)
        row = con.execute("select tenant_id from tenant.tenants where tenant_id = ?", [tenant_id]).fetchone()
        if row is None:
            return None
        con.execute(
            "update tenant.tenants set status = ?, updated_at = ? where tenant_id = ?",
            [status, now, tenant_id],
        )
    return get_tenant(tenant_id, db_path)


def delete_tenant(tenant_id: str, *, hard: bool = False, db_path: Path = DUCKDB_PATH) -> Tenant | None:
    """Soft-deletes by default (status -> STATUS_DELETED, row/schema/storage
    untouched - see module docstring). `hard=True` additionally drops the
    tenant's silo schema (a no-op for a pooled tenant, since there's no
    dedicated schema to drop) but still never touches raw storage - that
    stays a deliberate, separate operator action. Returns the tenant's final
    record (status='deleted'), or None if the tenant didn't exist."""
    tenant = get_tenant(tenant_id, db_path)
    if tenant is None:
        return None
    result = set_tenant_status(tenant_id, STATUS_DELETED, db_path)
    if hard and tenant.schema_name is not None:
        drop_tenant_schema(tenant_id, db_path=db_path)
    return result


def create_tenant_schema(tenant_id: str, db_path: Path = DUCKDB_PATH) -> str:
    """Idempotent - safe to call even if the tenant was created without
    isolation_policy='silo' at first and is being upgraded to it later.
    Returns the schema name. Populating this schema with the tenant's own
    copies of marts.*/ml.* is NOT done here - see module docstring."""
    tenant = get_tenant(tenant_id, db_path)
    if tenant is None:
        raise TenantError(f"tenant {tenant_id!r} not found")
    schema_name = tenant.schema_name or f"tenant_{tenant_id.replace('-', '_')}"
    with connect_with_retry(db_path) as con:
        con.execute(f"create schema if not exists {schema_name}")
        con.execute(
            "update tenant.tenants set isolation_policy = ?, schema_name = ?, updated_at = ? where tenant_id = ?",
            [ISOLATION_SILO, schema_name, utc_now(), tenant_id],
        )
    return schema_name


def drop_tenant_schema(tenant_id: str, db_path: Path = DUCKDB_PATH) -> None:
    tenant = get_tenant(tenant_id, db_path)
    if tenant is None or tenant.schema_name is None:
        return
    with connect_with_retry(db_path) as con:
        con.execute(f"drop schema if exists {tenant.schema_name} cascade")


def validate_tenant_access(requested_tenant_id: str, granted_tenant_id: str) -> bool:
    """True if a caller granted access to `granted_tenant_id` (e.g. from a
    JWT's tenant_id claim - see auth/auth_middleware.py) may act on
    `requested_tenant_id`. A plain equality check today (no cross-tenant
    admin override), kept as a function rather than an inline `==` so
    auth_middleware.py has one place to call and this module has one place
    to later add e.g. a platform-admin bypass without touching every call
    site."""
    return requested_tenant_id == granted_tenant_id


if __name__ == "__main__":
    demo = create_tenant("demo-retailer-group", "Demo Retailer Group")
    print(f"Created tenant {demo.tenant_id!r} (isolation={demo.isolation_policy}, storage={demo.storage_prefix})")
    print(f"All tenants: {[t.tenant_id for t in list_tenants()]}")
