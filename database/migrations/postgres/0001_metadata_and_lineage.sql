-- Metadata + lineage (PHASE7-DEPLOYMENT.md Section 6: "Neon/Postgres
-- (metadata + lineage + auth)"). Mirrors ingestion/metadata.py's DuckDB
-- table definitions (warehouse/duckdb/init.sql, upsert_ingestion_run(),
-- upsert_lineage_edges()) in Postgres DDL - same columns, same primary
-- keys, translated to Postgres types (varchar -> text, DuckDB's implicit
-- upsert-by-primary-key -> an explicit ON CONFLICT clause a caller's
-- INSERT would use against this schema).

create table if not exists ingestion_runs (
  run_id text primary key,
  source text,
  entity text,
  file_name text,
  source_path text,
  source_content_sha256 text,
  partition_path text,
  contract_name text,
  valid_count integer,
  invalid_count integer,
  schema_version text,
  valid_path text,
  quarantine_path text,
  metadata_path text,
  started_at timestamptz,
  completed_at timestamptz,
  duration_ms integer,
  status text
);

create table if not exists lineage_edges (
  run_id text,
  source_node text,
  target_node text,
  edge_type text,
  entity text,
  created_at timestamptz,
  primary key (run_id, source_node, target_node, edge_type)
);

create table if not exists elt_model_runs (
  model_name text,
  target_table text,
  load_strategy text,
  business_key text,
  source_row_count integer,
  affected_key_count integer,
  target_row_count integer,
  high_watermark timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  status text
);

create index if not exists idx_lineage_edges_run_id on lineage_edges (run_id);
create index if not exists idx_ingestion_runs_entity on ingestion_runs (entity);
