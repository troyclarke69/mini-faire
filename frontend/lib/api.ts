import type {
  AlertEvent,
  AnomalyClassification,
  AnomalyEvent,
  BrandContribution,
  Cluster,
  ComputeModelRun,
  EltModelRun,
  EventLagSummary,
  Forecast,
  IngestionRun,
  LineageEdge,
  MLFeature,
  ModelMetadata,
  MonitoringHealth,
  OrderProfitability,
  ProductReorderRisk,
  ProductVelocity,
  QuarantineRecord,
  Recommendation,
  RetailerCohortRetention,
  RetailerDaily,
  RetailerHealth,
  SchemaDriftEvent,
  StreamingStatus,
  SystemMetric,
  TenantDaily,
  TenantGrowth,
  TenantHealth,
  TenantSummary
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

// Revalidate window for every Server Component fetch through this file.
// Lowered from 30s to 5s in Phase 4 (PHASE4-REALTIME&STREAMING.md Section 6)
// so Live Mode's router.refresh() (see components/LiveModeProvider.tsx)
// actually surfaces fresh data instead of hitting Next's Data Cache within a
// stale 30s window - these are cheap local DuckDB reads through a local
// FastAPI dev server, so a shorter cache window costs little even for users
// who never turn Live Mode on.
const REVALIDATE_SECONDS = 5;

async function getJson<T>(path: string): Promise<T> {
  try {
    const response = await fetch(`${API_URL}${path}`, { next: { revalidate: REVALIDATE_SECONDS } });
    if (!response.ok) {
      return [] as T;
    }
    return (await response.json()) as T;
  } catch {
    return [] as T;
  }
}

// Phase 7 (PHASE7-DEPLOYMENT.md Section 4): the api/tenants/* routes are the
// first genuinely auth-gated part of this API (see api/tenant_api.py's
// module docstring) - every call needs `Authorization: Bearer <token>`,
// which a browser never attaches automatically to a Server Component's
// server-side fetch (unlike a cookie, which the browser sends but this
// fetch - running on the Node server, not in the browser - never sees
// either). Callers (app/tenants/page.tsx) get the token from
// lib/auth.ts's getSession() and pass it through explicitly.
//
// `cache: "no-store"` rather than getJson()'s revalidate window - this is
// per-user, per-tenant data; Next's shared Data Cache is the wrong place
// for it regardless of window length.
async function getJsonAuthed<T>(path: string, accessToken: string, fallback: T): Promise<T> {
  try {
    const response = await fetch(`${API_URL}${path}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      cache: "no-store"
    });
    if (!response.ok) {
      return fallback;
    }
    return (await response.json()) as T;
  } catch {
    return fallback;
  }
}

export const api = {
  retailerDaily: () => getJson<RetailerDaily[]>("/metrics/retailer-daily"),
  productVelocity: () => getJson<ProductVelocity[]>("/metrics/product-velocity"),
  orderProfitability: () => getJson<OrderProfitability[]>("/metrics/order-profitability"),
  retailerHealth: () => getJson<RetailerHealth[]>("/compute/retailer-health"),
  productReorderRisk: () => getJson<ProductReorderRisk[]>("/compute/product-reorder-risk"),
  brandContribution: () => getJson<BrandContribution[]>("/compute/brand-contribution"),
  retailerCohortRetention: () =>
    getJson<RetailerCohortRetention[]>("/compute/retailer-cohort-retention"),
  eventLagSummary: () => getJson<EventLagSummary[]>("/compute/event-lag-summary"),
  computeModelRuns: () => getJson<ComputeModelRun[]>("/compute/model-runs"),
  eltModelRuns: () => getJson<EltModelRun[]>("/metadata/elt-model-runs"),
  ingestionRuns: () => getJson<IngestionRun[]>("/metadata/ingestion-runs"),
  lineageEdges: () => getJson<LineageEdge[]>("/metadata/lineage-edges"),
  quarantineRecords: () => getJson<QuarantineRecord[]>("/metadata/quarantine-records"),
  // Phase 5 (PHASE5-MONITORING.md Section 5 / api/monitoring_api.py)
  systemMetrics: () => getJson<SystemMetric[]>("/monitoring/system-metrics"),
  anomalies: () => getJson<AnomalyEvent[]>("/monitoring/anomalies"),
  schemaDrift: () => getJson<SchemaDriftEvent[]>("/monitoring/schema-drift"),
  alerts: () => getJson<AlertEvent[]>("/monitoring/alerts"),
  // monitoringHealth/streamingStatus return a single object, not an array -
  // getJson()'s shared error/empty fallback (`[] as T`) still applies here.
  // An empty array cast as these object types just means every field reads
  // as `undefined` through optional chaining if the API call fails; every
  // consumer below is written to handle that (see components/monitoring/
  // StreamingStatus.tsx), so this reuses getJson() rather than adding a
  // second fetch helper just for two endpoints.
  monitoringHealth: () => getJson<MonitoringHealth>("/monitoring/health"),
  streamingStatus: () => getJson<StreamingStatus>("/monitoring/streaming-status"),
  // Phase 6 (PHASE6-ML.md Section 8 / api/ml_api.py)
  forecasts: () => getJson<Forecast[]>("/ml/forecasts"),
  clusters: () => getJson<Cluster[]>("/ml/clusters"),
  recommendations: () => getJson<Recommendation[]>("/ml/recommendations"),
  anomalyClassifications: () => getJson<AnomalyClassification[]>("/ml/anomalies/classified"),
  mlModels: () => getJson<ModelMetadata[]>("/ml/models"),
  mlFeatures: () => getJson<MLFeature[]>("/ml/features")
};

// Phase 7 (PHASE7-DEPLOYMENT.md Section 4): tenant-scoped, auth-gated
// fetchers - see api/tenant_api.py's module docstring for why this is
// deliberately narrower than `api` above (only the tenant-scoped tables
// warehouse/duckdb/tenant_elt.sql and compute/polars/tenant_metrics.py
// actually populate; no tenant-scoped counterpart of `api.retailerDaily()`
// etc. exists, since the pre-Phase-7 marts were never tenant-scoped to
// begin with - see app/tenants/page.tsx's header comment for the full
// scope reconciliation this implies for "wire tenant filtering into the
// existing dashboards").
export const authApi = {
  tenants: (accessToken: string) => getJsonAuthed<TenantSummary[]>("/tenants", accessToken, []),
  tenant: (tenantId: string, accessToken: string) =>
    getJsonAuthed<TenantSummary | null>(`/tenants/${encodeURIComponent(tenantId)}`, accessToken, null),
  tenantDaily: (tenantId: string, accessToken: string) =>
    getJsonAuthed<TenantDaily[]>(`/tenants/${encodeURIComponent(tenantId)}/daily`, accessToken, []),
  tenantHealth: (tenantId: string, accessToken: string) =>
    getJsonAuthed<TenantHealth | null>(`/tenants/${encodeURIComponent(tenantId)}/health`, accessToken, null),
  tenantGrowth: (tenantId: string, accessToken: string) =>
    getJsonAuthed<TenantGrowth | null>(`/tenants/${encodeURIComponent(tenantId)}/growth`, accessToken, null)
};

export function money(value: number | null | undefined) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value ?? 0);
}

export function number(value: number | null | undefined, digits = 0) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value ?? 0);
}

export function percent(value: number | null | undefined) {
  return `${number((value ?? 0) * 100, 1)}%`;
}

// Shared severity styling for the Phase 5 monitoring components
// (AnomalyTable, SchemaDriftTable, AlertsFeed) - one place so "critical"
// always reads the same color everywhere it appears, using this app's
// existing coral/marigold/mint palette (tailwind.config.ts) rather than
// introducing red/yellow/green outside it.
const SEVERITY_CLASSES: Record<string, string> = {
  critical: "bg-coral/10 text-coral border-coral/30 dark:bg-coral/10 dark:text-coral dark:border-coral/30",
  warning: "bg-marigold/10 text-marigold border-marigold/30 dark:bg-marigold/10 dark:text-marigold dark:border-marigold/30",
  info: "bg-slate-100 text-slate-600 border-slate-300 dark:bg-slate-900 dark:text-slate-300 dark:border-slate-700"
};

export function severityBadgeClasses(severity: string): string {
  return `inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
    SEVERITY_CLASSES[severity] ?? SEVERITY_CLASSES.info
  }`;
}

