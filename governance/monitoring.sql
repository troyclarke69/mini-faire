-- Phase 5 monitoring/alerting tables. Documentation-only mirror of the DDL each
-- owning module creates defensively at runtime (create schema/table if not exists),
-- same pattern as governance/ingestion_runs.sql mirrors ingestion_runs/lineage_edges/
-- elt_model_runs. The live source of truth for each table's DDL is:
--   anomalies.anomaly_events   -> anomalies/detector.py's _ensure_tables()
--   monitoring.system_metrics  -> monitoring/metrics.py's _ensure_tables()
--   monitoring.schema_drift_events -> monitoring/schema_drift.py's _ensure_tables()
--   monitoring.alert_events    -> alerts/dispatcher.py's _ensure_tables()

create schema if not exists anomalies;
create schema if not exists monitoring;

create table if not exists anomalies.anomaly_events (
  anomaly_id varchar primary key,
  anomaly_type varchar,
  severity varchar,
  detected_at timestamptz,
  entity_type varchar,
  entity_id varchar,
  metric_name varchar,
  metric_value double,
  baseline_value double,
  deviation double,
  method varchar,
  metadata varchar
);

create table if not exists monitoring.system_metrics (
  metric_id varchar primary key,
  metric_category varchar,
  metric_name varchar,
  metric_value double,
  unit varchar,
  computed_at timestamptz,
  window_start timestamptz,
  window_end timestamptz,
  metadata varchar
);

create table if not exists monitoring.schema_drift_events (
  drift_id varchar primary key,
  entity varchar,
  drift_type varchar,
  field_name varchar,
  expected varchar,
  actual varchar,
  severity varchar,
  detected_at timestamptz,
  source_path varchar,
  run_id varchar
);

create table if not exists monitoring.alert_events (
  alert_id varchar primary key,
  alert_type varchar,
  severity varchar,
  entity varchar,
  message varchar,
  metadata varchar,
  lineage_ref varchar,
  dashboard_url varchar,
  created_at timestamptz,
  dispatched_channels varchar
);
