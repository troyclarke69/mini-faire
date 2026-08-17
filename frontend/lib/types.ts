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

