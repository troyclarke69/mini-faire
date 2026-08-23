import { SimpleBarChart } from "@/components/charts/SimpleBarChart";
import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";
import { money, number } from "@/lib/api";
import type { SimulationState } from "@/lib/types";

// Renders api/simulation_api.py's GET /simulation/state snapshot -
// simulation/digital_twin.py's DigitalTwinState, read live off the
// warehouse (marts.dim_retailer/dim_product joined with
// compute_retailer_health/compute_product_reorder_risk for the classic
// twin - see that module's docstring). Two SimpleBarChart panels (reusing
// the exact component the retailer/product dashboards already use, rather
// than a bespoke chart) rank retailers by net_revenue and products by
// units_sold - "where is the marketplace's activity concentrated right
// now", the same question a scenario/counterfactual run's baseline branch
// starts from. Capped at TOP_N per chart with a documented count, matching
// this repo's "no silent caps" convention (e.g. app/ml/forecasts/page.tsx's
// CHARTS_PER_TYPE).

const TOP_N = 8;

export function DigitalTwinVisualizer({ state }: { state: SimulationState }) {
  const retailers = state.retailers ?? [];
  const products = state.products ?? [];
  const anomalies = state.recent_anomalies ?? [];

  const topRetailers = [...retailers]
    .sort((a, b) => b.net_revenue - a.net_revenue)
    .slice(0, TOP_N)
    .map((r) => ({ label: r.retailer_name ?? r.retailer_id, value: r.net_revenue }));

  const topProducts = [...products]
    .sort((a, b) => b.units_sold - a.units_sold)
    .slice(0, TOP_N)
    .map((p) => ({ label: p.product_name ?? p.product_id, value: p.units_sold }));

  return (
    <div className="space-y-4">
      {retailers.length === 0 && products.length === 0 ? (
        <EmptyState label="No digital twin state yet - run scripts/run_demo.py (or seed a tenant) so marts.dim_retailer/dim_product have rows to snapshot." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {topRetailers.length > 0 ? (
            <SimpleBarChart title={`Top ${topRetailers.length} Retailers by Net Revenue`} points={topRetailers} color="#70406f" />
          ) : (
            <EmptyState label="No retailer state in this twin." />
          )}
          {topProducts.length > 0 ? (
            <SimpleBarChart title={`Top ${topProducts.length} Products by Units Sold`} points={topProducts} color="#2e7d72" />
          ) : (
            <EmptyState label="No product state in this twin." />
          )}
        </div>
      )}
      {anomalies.length > 0 ? (
        <TablePanel title="Open Anomalies in the Twin" actions={<span className="text-xs text-slate-500">{anomalies.length} total</span>}>
          <table className="min-w-full">
            <thead className="table-head">
              <tr>
                {["Type", "Severity", "Entity", "Metric", "Detected"].map((heading) => (
                  <th key={heading} className="px-3 py-2">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {anomalies.slice(0, TOP_N).map((a) => (
                <tr key={a.anomaly_id}>
                  <td className="table-cell font-medium">{a.anomaly_type.replace(/_/g, " ")}</td>
                  <td className="table-cell">{a.severity}</td>
                  <td className="table-cell text-xs text-slate-500">
                    {a.entity_type}:{a.entity_id}
                  </td>
                  <td className="table-cell text-xs text-slate-500">
                    {a.metric_name} = {number(a.metric_value ?? undefined, 2)}
                  </td>
                  <td className="table-cell text-xs text-slate-500" title={a.detected_at ?? undefined}>
                    {a.detected_at ?? "n/a"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TablePanel>
      ) : null}
      <p className="text-xs text-slate-500">
        Twin as of {state.summary?.as_of ?? "n/a"} - GMV {money(state.summary?.gmv)}, {number(state.summary?.units_sold)} unit(s) sold,
        tick {state.summary?.tick ?? 0}.
      </p>
    </div>
  );
}
