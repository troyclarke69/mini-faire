import type {
  BrandContribution,
  ComputeModelRun,
  EltModelRun,
  EventLagSummary,
  IngestionRun,
  LineageEdge,
  OrderProfitability,
  ProductReorderRisk,
  ProductVelocity,
  QuarantineRecord,
  RetailerCohortRetention,
  RetailerDaily,
  RetailerHealth
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

async function getJson<T>(path: string): Promise<T> {
  try {
    const response = await fetch(`${API_URL}${path}`, { next: { revalidate: 30 } });
    if (!response.ok) {
      return [] as T;
    }
    return (await response.json()) as T;
  } catch {
    return [] as T;
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
  quarantineRecords: () => getJson<QuarantineRecord[]>("/metadata/quarantine-records")
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

