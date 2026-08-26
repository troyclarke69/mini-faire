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

// Phase 8 (PHASE8-SIMULATION.md) simulation/digital-twin types - api/
// simulation_api.py's response shapes. As with the Phase 5/6 types above,
// most nested detail on Scenario/Counterfactual is a JSON-encoded string
// (simulation.scenario_results / simulation.counterfactual_results are
// DuckDB varchar columns holding `json.dumps(...)` output - see
// simulation/scenario_engine.py's/simulation/counterfactuals.py's
// `_ensure_tables()`), not a pre-parsed object; parse with JSON.parse()
// where the extra detail is actually displayed (components/simulation/
// SimulationResultCharts.tsx). This is the shape GET /simulation/results
// and GET /simulation/results/scenario|counterfactual/{id} return (both
// read straight off those tables via api/db.py's query_safe()). The one
// exception is POST /simulation/scenarios / POST /simulation/counterfactuals'
// own immediate response - those come back as real, already-parsed JSON
// objects/arrays (FastAPI serializing a fresh ScenarioResult/
// CounterfactualResult dataclass, not a stored varchar row) - components/
// simulation/ScenarioBuilder.tsx and CounterfactualBuilder.tsx handle that
// freshly-computed shape inline rather than importing these types, since
// it's genuinely a different wire shape for the same logical entity.

export type SimulationRetailerState = {
  retailer_id: string;
  retailer_name: string | null;
  country: string | null;
  retailer_category: string | null;
  status: string | null;
  order_count: number;
  net_revenue: number;
  estimated_profit: number | null;
  last_order_ts: string | null;
  retailer_health_score: number | null;
};

export type SimulationProductState = {
  product_id: string;
  product_name: string | null;
  product_category: string | null;
  brand_id: string | null;
  unit_price: number | null;
  unit_cost: number | null;
  inventory_count: number | null;
  is_active: boolean | null;
  units_sold: number;
  inventory_velocity: number | null;
  reorder_risk_score: number | null;
  reorder_risk_band: string | null;
  last_sold_at: string | null;
};

export type SimulationAnomalyRecord = {
  anomaly_id: string;
  anomaly_type: string;
  severity: string;
  detected_at: string | null;
  entity_type: string;
  entity_id: string;
  metric_name: string;
  metric_value: number | null;
  baseline_value: number | null;
  deviation: number | null;
};

export type SimulationTwinSummary = {
  tenant_id: string | null;
  as_of: string;
  tick: number;
  retailer_count: number;
  product_count: number;
  gmv: number;
  units_sold: number;
  average_inventory_velocity: number | null;
  open_anomaly_count: number;
  active_forecast_count: number;
};

// GET /simulation/state
export type SimulationState = {
  summary: SimulationTwinSummary;
  retailers: SimulationRetailerState[];
  products: SimulationProductState[];
  recent_anomalies: SimulationAnomalyRecord[];
};

// GET /simulation/scenarios and /simulation/counterfactuals (catalog half) -
// scenario_engine.SCENARIO_PARAM_SCHEMA / counterfactuals.
// COUNTERFACTUAL_PARAM_SCHEMA verbatim: {param_name: "required" | "optional (...)"}.
export type SimulationParamSchema = Record<string, Record<string, string>>;

export type ScenarioCatalog = {
  scenario_types: string[];
  param_schema: SimulationParamSchema;
};

export type CounterfactualCatalog = {
  counterfactual_types: string[];
  param_schema: SimulationParamSchema;
};

// simulation.scenario_results / simulation.counterfactual_results rows, as
// GET /simulation/results and /simulation/results/scenario|counterfactual/{id}
// return them - see the module-level note above on the JSON-string fields.
export type Scenario = {
  scenario_id: string;
  scenario_type: string;
  tenant_id: string | null;
  params: string;
  ticks: number;
  predicted_gmv_baseline: number;
  predicted_gmv_scenario: number;
  predicted_gmv_delta: number;
  predicted_velocity_baseline: number | null;
  predicted_velocity_scenario: number | null;
  predicted_inventory: string;
  predicted_anomalies: string;
  predicted_retailer_health: string;
  predicted_cluster_movement: string;
  predicted_recommendations: string;
  started_at: string;
  completed_at: string;
  status: string;
};

export type Counterfactual = {
  counterfactual_id: string;
  counterfactual_type: string;
  tenant_id: string | null;
  params: string;
  replay_ticks: number;
  actual_order_count: number;
  counterfactual_order_count: number;
  actual_units_sold: number;
  counterfactual_units_sold: number;
  actual_gmv: number;
  counterfactual_gmv: number;
  counterfactual_gmv_delta: number;
  retailer_diffs: string;
  product_diffs: string;
  predicted_anomalies: string;
  predicted_cluster_movement: string;
  predicted_recommendations: string;
  removed_or_modified_order_ids: string;
  started_at: string;
  completed_at: string;
  status: string;
};

// A result feed entry is either kind - components/simulation/
// SimulationTimeline.tsx merges both into one time-ordered list.
export type SimulationResult = Scenario | Counterfactual;

// GET /simulation/results
export type SimulationResultsFeed = {
  scenarios: Scenario[];
  counterfactuals: Counterfactual[];
};

// GET /simulation/agents - not a persisted resource (simulation/
// scenario_engine.py's build_agents() builds fresh, ephemeral agent objects
// every run - see that function's docstring); this is the DEFAULT strategy
// field values plus the twin's current retailer/product id set, which
// components/simulation/AgentStrategyEditor.tsx renders and components/
// simulation/ScenarioBuilder.tsx's override fields are seeded from.
export type MarketplaceStrategyDefaults = {
  demand_shock_probability: number;
  demand_shock_magnitude: [number, number];
  seasonal_amplitude: number;
  seasonal_period_ticks: number;
  category_trend_drift: number;
  competitor_pressure_baseline: number;
};

export type RetailerStrategyDefaults = {
  pricing_strategy: string;
  inventory_strategy: string;
  promotion_strategy: string;
  fulfillment_strategy: string;
  anomaly_response_strategy: string;
  ml_driven: boolean;
  promotion_discount: number;
  promotion_every_n_ticks: number;
  reorder_threshold_units: number;
  reorder_quantity: number;
  fulfillment_cap_per_tick: number;
};

export type ProductStrategyDefaults = {
  price_elasticity: number;
  base_demand_per_tick: number;
  inventory_decay_rate: number;
  velocity_sensitivity: number;
};

export type AgentStrategy = {
  retailer_ids: string[];
  product_ids: string[];
  default_marketplace_strategy: MarketplaceStrategyDefaults;
  default_retailer_strategy: RetailerStrategyDefaults;
  default_product_strategy: ProductStrategyDefaults;
};

// Phase 9 (PHASE9-AUTONOMY.md) autonomous-agent types - api/autonomy_api.py's
// response shapes, mirroring autonomy/agent_framework.py's AgentAction/
// AgentConstraints dataclasses and orchestration/agent_flow.py's conflict
// record / run summary shapes verbatim. As with the Phase 5/6/8 types above,
// `params` is a JSON-encoded string (see AgentAction's own docstring: same
// "JSON blob in a varchar column" convention as simulation.scenario_results.
// params/ml.model_registry.params), not a pre-parsed object - parse with
// JSON.parse() where the extra detail is actually displayed (components/
// autonomy/AgentDecisionTable.tsx).

export type AgentAction = {
  action_id: string;
  agent_type: string; // "pricing" | "inventory" | "demand" | "anomaly_response" | "retailer_strategy"
  action_type: string;
  entity_type: string;
  entity_id: string;
  tenant_id: string | null;
  params: string;
  rationale: string;
  confidence: number;
  status: string; // "proposed" | "applied" | "rejected" | "reverted"
  reward: number | null;
  run_id: string | null;
  created_at: string;
  applied_at: string | null;
};

// GET /autonomy/actions and its five per-agent-type siblings
// (/autonomy/pricing, /inventory, /demand, /anomalies, /retailer-strategy)
// all return this same {"actions": [...]} envelope.
export type AgentActionsFeed = {
  actions: AgentAction[];
};

// One row of autonomy.conflicts - orchestration/agent_flow.py's
// _resolve_and_apply() builds this exact shape the moment a later proposal
// loses cooldown_entities arbitration to an earlier one; POST /autonomy/run's
// own `conflicts` field (before the row is even persisted) carries the same
// shape.
export type AgentConflict = {
  conflict_id: string;
  run_id: string;
  entity_type: string;
  entity_id: string;
  winning_agent_type: string | null;
  winning_action_type: string | null;
  winning_action_id: string | null;
  rejected_agent_type: string;
  rejected_action_type: string;
  rejected_action_id: string;
  created_at: string;
};

// GET /autonomy/conflicts
export type AgentConflictsFeed = {
  conflicts: AgentConflict[];
};

// Per-agent-type aggregate api/autonomy_api.py's _compute_performance()
// computes straight off each autonomy.*_actions table's status/reward
// columns - GET /autonomy/performance and the WS/SSE "performance" snapshot
// attached to every non-empty update.
export type AgentPerformance = {
  agent_type: string;
  action_count: number;
  applied_count: number;
  rejected_count: number;
  advisory_count: number;
  average_reward: number | null;
};

// GET /autonomy/performance
export type AgentPerformanceFeed = {
  performance: AgentPerformance[];
};

// autonomy/agent_framework.py's AgentConstraints dataclass, as
// `dataclasses.asdict()` serializes it for GET /autonomy/state's
// default_constraints field - the same safety limits every agent's
// enforce_constraints() call respects (max price move, reorder multiplier
// cap, promotion discount cap, per-run action cap).
export type AgentConstraints = {
  max_price_change_pct: number;
  min_unit_price: number;
  max_reorder_multiplier: number;
  max_actions_per_agent_per_run: number;
  max_promotion_discount: number;
};

// A narrowed elt_model_runs row - api/autonomy_api.py's
// _last_run_by_agent_type() reads only these five columns (load_strategy=
// 'autonomy_agent' rows - see orchestration/agent_flow.py's
// _append_agent_run()), keyed by agent_type in GET /autonomy/state's
// last_run_by_agent_type field below.
export type AgentRunRecord = {
  model_name: string;
  target_table: string;
  started_at: string;
  completed_at: string;
  status: string;
};

// GET /autonomy/state. Not live in-memory agent state (agent instances are
// ephemeral, built fresh once per orchestration/agent_flow.py run - see
// agent_framework.BaseAutonomousAgent's docstring) - this is the twin
// summary, the fixed conflict-resolution priority order, the default safety
// constraints, the current pipeline-health reading, and the last recorded
// run per agent_type. components/autonomy/AgentStateVisualizer.tsx's data
// source.
export type AutonomyState = {
  twin_summary: SimulationTwinSummary;
  pipeline_healthy: boolean;
  agent_type_priority: string[];
  default_constraints: AgentConstraints;
  last_run_by_agent_type: Record<string, AgentRunRecord>;
};

// POST /autonomy/run's response - orchestration/agent_flow.py's
// run_agent_flow() summary dict verbatim. `conflicts` here is this run's own
// freshly-resolved list (same AgentConflict shape as the persisted
// autonomy.conflicts rows GET /autonomy/conflicts returns, just not yet
// round-tripped through the table). Can come back as `{}` (every field
// `undefined`) if the warehouse isn't built yet - components/autonomy/
// AgentRunTrigger.tsx renders defensively for that case, same "empty object
// means nothing to show yet" posture other Phase 8 detail lookups already
// take.
export type AgentRunSummary = {
  run_id: string;
  mode: string;
  rounds: number;
  pipeline_healthy: boolean;
  proposed_count: number;
  applied_count: number;
  advisory_count: number;
  rejected_count: number;
  conflicts: AgentConflict[];
  gmv_before: number;
  gmv_after: number;
  reward: number;
  action_counts_by_agent: Record<string, number>;
  elapsed_seconds: number;
};

