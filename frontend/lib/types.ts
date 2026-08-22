export type RetailerDaily = {
  order_date: string;
  retailer_id: string;
  retailer_name: string;
  order_count: number;
  units_sold: number;
  gmv: number;
  net_revenue: number;
  estimated_profit: number;
  average_order_value: number;
};

export type ProductVelocity = {
  product_id: string;
  product_name: string;
  product_category: string;
  order_count: number;
  units_sold: number;
  inventory_count: number;
  inventory_velocity: number | null;
};

export type OrderProfitability = {
  order_id: string;
  retailer_id: string;
  product_id: string;
  order_date: string;
  quantity: number;
  gross_amount: number;
  discount_amount: number;
  net_amount: number;
  estimated_cost: number;
  estimated_profit: number;
  estimated_margin: number | null;
};

export type RetailerHealth = {
  retailer_id: string;
  order_count: number;
  net_revenue: number;
  estimated_profit: number;
  last_order_ts: string;
  retailer_health_score: number;
};

export type ProductReorderRisk = {
  product_id: string;
  product_name: string;
  brand_id: string;
  product_category: string;
  inventory_count: number;
  units_sold: number;
  last_sold_at: string | null;
  sell_through_rate: number;
  reorder_risk_score: number;
  reorder_risk_band: string;
};

export type BrandContribution = {
  brand_id: string;
  product_category: string;
  order_count: number;
  units_sold: number;
  gmv: number;
  net_revenue: number;
  estimated_profit: number;
  estimated_margin: number | null;
};

export type RetailerCohortRetention = {
  signup_month: string;
  order_month: string | null;
  active_retailers: number;
  order_count: number;
  net_revenue: number | null;
  cohort_age_months: number | null;
};

export type EventLagSummary = {
  event_type: string;
  event_count: number;
  min_lag_seconds: number;
  avg_lag_seconds: number;
  max_lag_seconds: number;
};

export type ComputeModelRun = {
  model_name: string;
  target_table: string;
  source_tables: string;
  row_count: number;
  column_count: number;
  computed_at: string;
  status: string;
};

export type EltModelRun = {
  model_name: string;
  target_table: string;
  load_strategy: string;
  business_key: string;
  source_row_count: number;
  affected_key_count: number;
  target_row_count: number;
  high_watermark: string | null;
  started_at: string;
  completed_at: string;
  status: string;
};

export type IngestionRun = {
  run_id: string;
  source: string;
  entity: string;
  file_name: string;
  source_path: string;
  source_content_sha256: string;
  partition_path: string;
  contract_name: string;
  valid_count: number;
  invalid_count: number;
  schema_version: string;
  valid_path: string;
  quarantine_path: string;
  metadata_path: string;
  started_at: string;
  completed_at: string;
  duration_ms: number;
  status: string;
};

export type LineageEdge = {
  run_id: string;
  source_node: string;
  target_node: string;
  edge_type: string;
  entity: string;
  created_at: string;
};

export type QuarantineRecord = {
  path: string;
  run_id: string;
  entity: string;
  record_index: number;
  record: Record<string, unknown>;
  errors: { path: string; message: string }[];
};

// Phase 5 (PHASE5-MONITORING.md) monitoring types. `metadata` fields are
// JSON-encoded strings (DuckDB varchar columns, see anomalies/detector.py,
// monitoring/metrics.py, alerts/dispatcher.py) - parse with JSON.parse()
// where the extra detail is actually displayed, matching how
// QuarantineRecord's `record`/`errors` are already handled elsewhere in this
// file except pre-parsed on the Python side; these are left as strings here
// since each metadata shape varies by anomaly_type/metric_name/alert_type.
export type AnomalyEvent = {
  anomaly_id: string;
  anomaly_type: string;
  severity: string;
  detected_at: string;
  entity_type: string;
  entity_id: string;
  metric_name: string;
  metric_value: number;
  baseline_value: number | null;
  deviation: number | null;
  method: string;
  metadata: string;
};

export type SystemMetric = {
  metric_id: string;
  metric_category: string;
  metric_name: string;
  metric_value: number;
  unit: string;
  computed_at: string;
  window_start: string | null;
  window_end: string | null;
  metadata: string;
};

export type SchemaDriftEvent = {
  drift_id: string;
  entity: string;
  drift_type: string;
  field_name: string;
  expected: string;
  actual: string;
  severity: string;
  detected_at: string;
  source_path: string;
  run_id: string;
};

// Shape of api/realtime_api.py's / api/monitoring_api.py's heartbeat_status()
// (ingestion/heartbeat.py) results, as returned by /monitoring/health and
// /monitoring/streaming-status.
export type ServiceHeartbeat = {
  service: string;
  status: "running" | "stale" | "not_running";
  last_heartbeat_at: string | null;
  detail?: Record<string, unknown>;
};

export type StreamingStatus = {
  stream_generator: ServiceHeartbeat;
  mongo_change_stream: ServiceHeartbeat;
  realtime_flow: ServiceHeartbeat;
};

export type MonitoringHealth = {
  status: string;
  warehouse_path: string;
  services: StreamingStatus;
  any_streaming_service_running: boolean;
  anomalies_last_hour: number;
  critical_anomalies_last_hour: number;
  alerts_last_hour: number;
};

export type AlertEvent = {
  alert_id: string;
  alert_type: string;
  severity: string;
  entity: string;
  message: string;
  metadata: string;
  lineage_ref: string | null;
  dashboard_url: string;
  created_at: string;
  dispatched_channels: string;
};

// Phase 6 (PHASE6-ML.md) ML layer types. As with the Phase 5 monitoring
// types above, `metadata`/`params`/`metrics`/`feature_schema`/`features`
// fields are JSON-encoded strings (DuckDB varchar columns - see
// ml/registry.py, ml/models/*.py, ml/features/build_features.py) rather than
// pre-parsed objects, since each shape varies by forecast_type/entity_type/
// model_name; parse with JSON.parse() where the extra detail is actually
// displayed.

export type Forecast = {
  forecast_id: string;
  forecast_type: string;
  entity_type: string;
  entity_id: string;
  target_date: string;
  forecast_value: number;
  lower_bound: number;
  upper_bound: number;
  model_name: string;
  model_version: number | null;
  generated_at: string;
  horizon_days: number;
  metadata: string;
};

export type Cluster = {
  cluster_id: string;
  entity_type: string;
  entity_id: string;
  cluster_label: number;
  segment_name: string;
  plot_x: number;
  plot_y: number;
  method: string;
  model_name: string;
  model_version: number | null;
  computed_at: string;
  metadata: string;
};

export type Recommendation = {
  recommendation_id: string;
  recommendation_type: string;
  source_entity_type: string;
  source_entity_id: string;
  recommended_entity_type: string;
  recommended_entity_id: string;
  score: number;
  rank: number;
  method: string;
  model_name: string;
  model_version: number | null;
  generated_at: string;
  metadata: string;
};

export type AnomalyClassification = {
  classification_id: string;
  anomaly_id: string;
  predicted_type: string;
  confidence: number;
  actual_type: string;
  agrees_with_detector: boolean;
  model_name: string;
  model_version: number | null;
  classified_at: string;
  metadata: string;
};

// ml/registry.py's status vocabulary: "active" (currently live), "inactive"
// (registered, never promoted), "superseded" (was active, demoted by a
// newer promotion), "rolled_back" (was active, demoted by an explicit
// rollback). ModelRegistryTable renders each distinctly - see
// components/ml/ModelRegistryTable.tsx.
export type ModelStatus = "active" | "inactive" | "superseded" | "rolled_back";

export type ModelMetadata = {
  model_id: string;
  model_name: string;
  model_type: string;
  version: number;
  status: ModelStatus;
  params: string;
  metrics: string;
  feature_schema: string;
  artifact_path: string | null;
  trained_at: string;
  created_at: string;
};

// Phase 7 (PHASE7-DEPLOYMENT.md Section 2/4) tenant types - api/tenant_api.py's
// response shapes, mirroring multi_tenant/tenant_manager.py's Tenant
// dataclass and warehouse/duckdb/tenant_elt.sql's / compute/polars/
// tenant_metrics.py's tenant-scoped tables.
export type TenantSummary = {
  tenant_id: string;
  name: string;
  status: string;
  isolation_policy: string;
  created_at: string;
};

export type TenantDaily = {
  tenant_id: string;
  order_date: string;
  order_count: number;
  units_sold: number;
  gmv: number;
  net_revenue: number;
  average_order_value: number;
};

export type TenantHealth = {
  tenant_id: string;
  order_count: number;
  gmv: number;
  net_revenue: number;
  last_order_date: string;
  tenant_health_score: number;
};

export type TenantGrowth = {
  tenant_id: string;
  as_of_date: string;
  trailing_7d_gmv: number;
  prior_7d_gmv: number | null;
  growth_rate: number | null;
  trend: "growing" | "declining" | "flat" | "insufficient_history";
};

export type MLFeature = {
  feature_id: string;
  entity_type: string;
  entity_id: string;
  feature_group: string;
  computed_at: string;
  window_start: string | null;
  window_end: string | null;
  features: string;
};

