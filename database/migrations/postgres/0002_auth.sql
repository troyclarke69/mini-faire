-- Auth (PHASE7-DEPLOYMENT.md Section 6: "...+ auth"). Mirrors
-- auth/auth_models.py's DuckDB `auth.users` / `auth.refresh_tokens` tables
-- exactly - same columns/keys, Postgres schema+types.

create schema if not exists auth;

create table if not exists auth.users (
  user_id text primary key,
  email text unique not null,
  password_hash text not null,
  name text,
  role text not null,
  tenant_id text not null,
  status text not null,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  last_login_at timestamptz
);

create table if not exists auth.refresh_tokens (
  token_id text primary key,
  user_id text not null references auth.users (user_id),
  issued_at timestamptz not null,
  expires_at timestamptz not null,
  revoked boolean not null default false
);

create index if not exists idx_auth_users_tenant_id on auth.users (tenant_id);
create index if not exists idx_auth_refresh_tokens_user_id on auth.refresh_tokens (user_id);
