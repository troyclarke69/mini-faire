"""Tenant-aware ingestion (PHASE7-DEPLOYMENT.md Section 2).

A thin tenant-tagging wrapper around this repo's existing validate ->
quarantine pipeline (`ingestion/validate.py`, `ingestion/quarantine.py`),
not a parallel ingestion engine - the same contracts (`CONTRACT_BY_ENTITY`),
the same JSON-array-or-object loader, the same valid/quarantine split apply
here unchanged. What this module adds on top:

- every valid record gets a `tenant_id` field injected before it's written,
  so a downstream reader (`refresh_tenant_raw_tables()` below, or a direct
  `read_json_auto()` over the tenant raw zone) can tell whose row it is
  without re-deriving it from the file path;
- files land under `ingestion/paths.py`'s `TENANTS_RAW_DIR / <tenant_id> /
  <entity> / ...` instead of the single-tenant `RAW_DIR`, so a tenant's raw
  data is physically separable (see `multi_tenant/tenant_manager.py`'s
  module docstring on the pooled-vs-silo isolation policy this reflects);
- every ingest call is checked against `multi_tenant/tenant_manager.py`'s
  `validate_tenant_access()` before anything is written, so a caller acting
  on behalf of one tenant (e.g. an authenticated request whose JWT carries
  `tenant_id=A`) can't write into another tenant's raw zone by passing a
  different `tenant_id` argument;
- lineage edges (`ingestion/metadata.py`'s `upsert_lineage_edges()`) are
  tagged with the tenant in both `entity` (`"<tenant_id>:<entity>"`) and
  `run_id`, so `governance/lineage.md`'s lineage graph and the `/lineage`
  frontend page can distinguish tenant-scoped runs from the classic
  single-tenant ones without a schema change to `lineage_edges` itself.

Scope: this module is deliberately entity-generic (any `CONTRACT_BY_ENTITY`
key can be ingested per-tenant - a tenant onboarding wizard sending its own
retailers/products/orders/events all go through the same functions below).
Downstream of ingestion, though, `warehouse/duckdb/tenant_elt.sql`,
`compute/polars/tenant_metrics.py`, and `ml/tenant_models/` only build out
the "orders" path end to end (tenant GMV/order metrics/forecasts) - the
concrete, valuable "tenant usage" story a SaaS dashboard needs - rather than
mirroring all eight entity types through staging/marts/ML too. A tenant's
raw `retailers`/`products`/`events` records are still validated, tenant
raw ingestion is required to be tenant_admin+ for that tenant (or a platform
admin) - see auth_middleware.py's `require_role`/`require_tenant`, which
this module does not itself import (it stays framework-agnostic; the
FastAPI-facing enforcement belongs to the layer calling this)."""

from __future__ import annotations

import glob as glob_module
from pathlib import Path

import duckdb

from ingestion.duckdb_utils import connect_with_retry
from ingestion.metadata import (
    IngestionRun,
    LineageEdge,
    duration_ms,
    file_sha256,
    upsert_ingestion_run,
    upsert_lineage_edges,
    utc_now,
    write_metadata,
)
from ingestion.paths import DUCKDB_PATH, TENANTS_RAW_DIR
from ingestion.quarantine import write_valid_and_quarantine
from ingestion.validate import CONTRACT_BY_ENTITY, load_json_records, validate_records
from multi_tenant.tenant_manager import (
    STATUS_ACTIVE,
    TenantError,
    get_tenant,
    tenant_storage_path,
    validate_tenant_access,
    validate_tenant_id,
)

SQL_DIR = Path(__file__).resolve().parents[1] / "warehouse" / "duckdb"

# Only "orders" is carried all the way through tenant_elt.sql / tenant_metrics.py /
# ml/tenant_models/ - see module docstring. Ingestion itself accepts any
# CONTRACT_BY_ENTITY key; this constant is just what refresh_tenant_raw_tables()
# builds by default.
DEFAULT_TENANT_ENTITIES = ("orders",)

# Same purpose as ingestion/load_duckdb.py's EMPTY_TABLE_COLUMNS: a raw table
# still has to exist (with the right columns, plus tenant_id) even when a
# tenant hasn't ingested anything yet, so downstream SQL never has to special-
# case "table missing" vs "table empty". Kept to entities this module's
# downstream ELT actually reads (see DEFAULT_TENANT_ENTITIES); other entities
# fall back to a generic tenant_id + json-blob shape via _fallback_columns().
TENANT_EMPTY_TABLE_COLUMNS: dict[str, str] = {
    "orders": (
        "tenant_id varchar, order_id varchar, retailer_id varchar, product_id varchar, "
        "order_ts varchar, quantity integer, gross_amount double, discount_amount double, status varchar"
    ),
}


def _fallback_columns(entity: str) -> str:
    return TENANT_EMPTY_TABLE_COLUMNS.get(entity, "tenant_id varchar, record varchar")


def _require_active_tenant(tenant_id: str, *, caller_tenant_id: str | None) -> None:
    """Raises TenantError unless `tenant_id` names a real, active tenant and
    (when `caller_tenant_id` is given - i.e. this call is on behalf of an
    authenticated request, not an internal/orchestration call) the caller is
    actually authorized for that tenant. `caller_tenant_id=None` is the
    "trusted internal caller" escape hatch (e.g. a maintenance script acting
    directly with an operator-supplied tenant_id) - the same shape as
    `auth/auth_middleware.py`'s `require_tenant()` allowing platform `admin`
    callers through unchecked, just at the ingestion layer instead of the
    HTTP layer."""
    validate_tenant_id(tenant_id)
    if caller_tenant_id is not None and not validate_tenant_access(tenant_id, caller_tenant_id):
        raise TenantError(f"caller (tenant {caller_tenant_id!r}) is not authorized to ingest for tenant {tenant_id!r}")
    tenant = get_tenant(tenant_id)
    if tenant is None:
        raise TenantError(f"tenant {tenant_id!r} not found")
    if tenant.status != STATUS_ACTIVE:
        raise TenantError(f"tenant {tenant_id!r} is not active (status={tenant.status!r})")


def tenant_event_run_id(tenant_id: str, entity: str, path: Path) -> str:
    return f"tenant_{tenant_id}_{entity}_{path.stem}"


def ingest_tenant_file(
    tenant_id: str,
    entity: str,
    path: Path,
    *,
    caller_tenant_id: str | None = None,
    run_id: str | None = None,
) -> IngestionRun:
    """Validates `path`'s records against `entity`'s existing JSON-schema
    contract, tags every valid record with `tenant_id`, and writes the
    valid/quarantine split under this tenant's raw zone
    (`multi_tenant/tenant_manager.py`'s `tenant_storage_path()`). Mirrors
    `ingestion/event_ingestion.py`'s `ingest_event_file()` structure closely
    on purpose - same metadata/lineage bookkeeping, just tenant-scoped."""
    if entity not in CONTRACT_BY_ENTITY:
        raise TenantError(f"unknown entity {entity!r} - not in ingestion.validate.CONTRACT_BY_ENTITY")
    _require_active_tenant(tenant_id, caller_tenant_id=caller_tenant_id)

    started_at = utc_now()
    run_id = run_id or tenant_event_run_id(tenant_id, entity, path)
    records = load_json_records(path)
    result = validate_records(entity, records)
    tagged_valid = [{**record, "tenant_id": tenant_id} for record in result.valid_records]

    base = tenant_storage_path(tenant_id, entity, run_id)
    valid_path = base / "valid" / path.name
    quarantine_path = base / "quarantine" / path.name
    metadata_path = base / "metadata" / "ingestion_run.json"

    write_valid_and_quarantine(
        valid_path=valid_path,
        quarantine_path=quarantine_path,
        valid_records=tagged_valid,
        invalid_records=result.invalid_records,
    )

    completed_at = utc_now()
    status = "success" if not result.invalid_records else "completed_with_quarantine"
    run = IngestionRun(
        run_id=run_id,
        source="tenant",
        entity=entity,
        file_name=str(path),
        source_path=str(path),
        source_content_sha256=file_sha256(path),
        partition_path=f"tenants/{tenant_id}/{entity}",
        contract_name=CONTRACT_BY_ENTITY[entity],
        valid_count=len(tagged_valid),
        invalid_count=len(result.invalid_records),
        schema_version="2020-12",
        valid_path=str(valid_path),
        quarantine_path=str(quarantine_path),
        metadata_path=str(metadata_path),
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms(started_at, completed_at),
        status=status,
    )
    write_metadata(metadata_path, run)
    upsert_ingestion_run(run)
    tenant_entity = f"{tenant_id}:{entity}"
    upsert_lineage_edges(
        [
            LineageEdge(
                run_id=run_id, source_node=str(path), target_node=str(valid_path),
                edge_type="tenant_validated_to_valid_raw", entity=tenant_entity, created_at=completed_at,
            ),
            LineageEdge(
                run_id=run_id, source_node=str(path), target_node=str(quarantine_path),
                edge_type="tenant_validated_to_quarantine", entity=tenant_entity, created_at=completed_at,
            ),
            LineageEdge(
                run_id=run_id, source_node=str(valid_path), target_node=f"raw.raw_tenant_{entity}",
                edge_type="tenant_loaded_to_raw_table", entity=tenant_entity, created_at=completed_at,
            ),
        ]
    )
    return run


def ingest_tenant_directory(
    tenant_id: str, entity: str, root: Path | None = None, *, caller_tenant_id: str | None = None
) -> list[IngestionRun]:
    """Ingests every `*.json` file under `root` (default: this tenant's own
    inbox, `tenant_storage_path(tenant_id, entity, "inbox")` - a caller
    drops files there before calling this, e.g. a bulk CSV/JSON upload from
    the onboarding wizard) as this tenant. Access is validated once up front
    (`_require_active_tenant`) rather than once per file, since every file in
    the batch is ingested under the same tenant_id."""
    _require_active_tenant(tenant_id, caller_tenant_id=caller_tenant_id)
    root = root or tenant_storage_path(tenant_id, entity, "inbox")
    if not root.exists():
        return []
    return [
        ingest_tenant_file(tenant_id, entity, path, caller_tenant_id=caller_tenant_id)
        for path in sorted(root.glob("**/*.json"))
    ]


def _resolve_files(pattern: Path) -> list[str]:
    seen: set[str] = set()
    resolved: list[str] = []
    for file_path in glob_module.glob(str(pattern), recursive=True):
        normalized = file_path.replace("\\", "/")
        if normalized not in seen:
            seen.add(normalized)
            resolved.append(normalized)
    return resolved


def refresh_tenant_raw_tables(entities: tuple[str, ...] = DEFAULT_TENANT_ENTITIES, db_path: Path = DUCKDB_PATH) -> dict[str, int]:
    """(Re)builds `raw.raw_tenant_<entity>` from every tenant's valid files
    under `TENANTS_RAW_DIR`, unioned across tenants (each record already
    carries its own `tenant_id` column, tagged by `ingest_tenant_file()`
    above) - the tenant-scoped counterpart of `ingestion/load_duckdb.py`'s
    `load_valid_json_to_staging()`. Returns `{entity: row_count}`. Safe to
    call before any tenant has ever ingested anything - each table still
    gets created (empty, with `TENANT_EMPTY_TABLE_COLUMNS`' shape) so
    `warehouse/duckdb/tenant_elt.sql` never has to special-case a missing
    table."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    row_counts: dict[str, int] = {}
    with connect_with_retry(db_path) as con:
        con.execute("create schema if not exists raw")
        for entity in entities:
            table = f"raw_tenant_{entity}"
            con.execute(f"drop table if exists raw.{table}")
            pattern = TENANTS_RAW_DIR / "*" / entity / "**" / "valid" / "*.json"
            files = _resolve_files(pattern)
            if files:
                file_list = ", ".join("'" + f.replace("'", "''") + "'" for f in files)
                con.execute(
                    f"create table raw.{table} as select * from read_json_auto([{file_list}], union_by_name=true)"
                )
                row_counts[entity] = con.execute(f"select count(*) from raw.{table}").fetchone()[0]
            else:
                con.execute(f"create table raw.{table} ({_fallback_columns(entity)})")
                row_counts[entity] = 0
    return row_counts


def execute_sql_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    con.execute(path.read_text(encoding="utf-8"))


def rebuild_tenant_warehouse(db_path: Path = DUCKDB_PATH) -> dict[str, int]:
    """Full tenant-warehouse refresh: raw.raw_tenant_* tables from every
    tenant's ingested files, then `warehouse/duckdb/tenant_elt.sql`'s
    staging/marts build on top. Called standalone (this module's __main__,
    or an operator/orchestration script) rather than folded into
    `ingestion/load_duckdb.py`'s `rebuild_warehouse()` - the two warehouses
    (classic single-tenant, tenant-scoped) are independent build paths that
    happen to share one DuckDB file, so a caller that only cares about one
    doesn't pay for rebuilding the other."""
    row_counts = refresh_tenant_raw_tables(db_path=db_path)
    with connect_with_retry(db_path) as con:
        execute_sql_file(con, SQL_DIR / "tenant_elt.sql")
    return row_counts


if __name__ == "__main__":
    counts = rebuild_tenant_warehouse()
    print(f"Rebuilt tenant warehouse: {counts}")
