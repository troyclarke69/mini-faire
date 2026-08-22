-- Tenants (PHASE7-DEPLOYMENT.md Section 2/6). Mirrors
-- multi_tenant/tenant_manager.py's DuckDB `tenant.tenants` table exactly.

create schema if not exists tenant;

create table if not exists tenant.tenants (
  tenant_id text primary key,
  name text not null,
  status text not null,
  isolation_policy text not null,
  storage_prefix text not null,
  schema_name text,
  metadata jsonb,
  created_at timestamptz not null,
  updated_at timestamptz not null
);

create index if not exists idx_tenants_status on tenant.tenants (status);
