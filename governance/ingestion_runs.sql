create table if not exists ingestion_runs (
  run_id varchar primary key,
  source varchar,
  entity varchar,
  file_name varchar,
  source_path varchar,
  source_content_sha256 varchar,
  partition_path varchar,
  contract_name varchar,
  valid_count integer,
  invalid_count integer,
  schema_version varchar,
  valid_path varchar,
  quarantine_path varchar,
  metadata_path varchar,
  started_at timestamptz,
  completed_at timestamptz,
  duration_ms integer,
  status varchar
);

create table if not exists lineage_edges (
  run_id varchar,
  source_node varchar,
  target_node varchar,
  edge_type varchar,
  entity varchar,
  created_at timestamptz,
  primary key (run_id, source_node, target_node, edge_type)
);

create table if not exists elt_model_runs (
  model_name varchar,
  target_table varchar,
  load_strategy varchar,
  business_key varchar,
  source_row_count integer,
  affected_key_count integer,
  target_row_count integer,
  high_watermark timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  status varchar
);
