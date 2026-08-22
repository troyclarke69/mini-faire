import { ForecastChart } from "@/components/ml/ForecastChart";
import { MLLiveBar } from "@/components/ml/MLLiveBar";
import { MLTabs } from "@/components/ml/MLTabs";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";

// forecast_top_n_entities (config/ml.yaml, default 10) means every
// per-entity forecast_type can have up to 10 series - showing all of them
// on one static page would be a wall of charts, so this caps each
// forecast_type at CHARTS_PER_TYPE representative series and says so
// explicitly (see the "no silent caps" note this repo follows elsewhere,
// e.g. ml/features/build_features.py's MAX_ORDER_FEATURE_ROWS) rather than
// silently dropping the rest.
const CHARTS_PER_TYPE = 3;

const TYPE_LABEL: Record<string, string> = {
  gmv_daily: "GMV - daily, marketplace-wide",
  gmv_weekly: "GMV - weekly, marketplace-wide",
  gmv_retailer: "GMV by retailer",
  velocity_product: "Order velocity by product",
  velocity_retailer: "Order velocity by retailer",
  inventory_level: "Inventory level by product (stockout / reorder thresholds)",
  price_trend: "Price trend by product"
};

const TYPE_ORDER = ["gmv_daily", "gmv_weekly", "gmv_retailer", "velocity_product", "velocity_retailer", "inventory_level", "price_trend"];

export default async function MLForecastsPage() {
  const rows = await api.forecasts();

  const seriesByKey = new Map<string, typeof rows>();
  for (const row of rows) {
    const key = `${row.forecast_type}::${row.entity_id}`;
    if (!seriesByKey.has(key)) seriesByKey.set(key, []);
    seriesByKey.get(key)!.push(row);
  }

  const keysByType = new Map<string, string[]>();
  for (const key of seriesByKey.keys()) {
    const type = key.split("::")[0];
    if (!keysByType.has(type)) keysByType.set(type, []);
    keysByType.get(type)!.push(key);
  }
  const orderedTypes = [
    ...TYPE_ORDER.filter((type) => keysByType.has(type)),
    ...Array.from(keysByType.keys()).filter((type) => !TYPE_ORDER.includes(type))
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Forecasts"
        subtitle="GMV, order velocity, inventory, and price forecasts (ml/models/forecasting.py) - point estimate with an empirical ~80% confidence band, not a rigorous prediction interval."
      />
      <MLTabs />
      <MLLiveBar />
      {rows.length === 0 ? (
        <EmptyState label="No forecasts generated yet - run `python orchestration/ml_training_flow.py` then `python orchestration/ml_inference_flow.py`." />
      ) : (
        orderedTypes.map((type) => {
          const keys = keysByType.get(type)!;
          const visibleKeys = keys.slice(0, CHARTS_PER_TYPE);
          return (
            <section key={type} className="space-y-3">
              <h2 className="text-sm font-semibold text-slate-950 dark:text-white">{TYPE_LABEL[type] ?? type.replace(/_/g, " ")}</h2>
              <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
                {visibleKeys.map((key) => (
                  <ForecastChart key={key} title={key.split("::")[1]} series={seriesByKey.get(key)!} />
                ))}
              </div>
              {keys.length > visibleKeys.length ? (
                <p className="text-xs text-slate-500">
                  Showing {visibleKeys.length} of {keys.length} series for this forecast type.
                </p>
              ) : null}
            </section>
          );
        })
      )}
    </div>
  );
}
