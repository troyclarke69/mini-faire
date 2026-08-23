import { SimpleBarChart } from "@/components/charts/SimpleBarChart";
import { TablePanel } from "@/components/TablePanel";
import { money, number } from "@/lib/api";
import type { Counterfactual, Scenario } from "@/lib/types";

// PHASE8-SIMULATION.md Section 7's SimulationResultCharts - the detail view
// for one persisted simulation.scenario_results / simulation.
// counterfactual_results row (app/simulation/results/page.tsx?scenario=...
// or ?counterfactual=...). Every nested field on these two types is a
// JSON-encoded string, not a pre-parsed object (see lib/types.ts's
// module-level note above the Scenario/Counterfactual types) - parsed here
// with the same try/catch-and-fall-back-to-empty convention components/ml/
// ModelRegistryTable.tsx's summarizeMetrics() already establishes, since a
// malformed or unexpectedly-shaped blob shouldn't crash the whole detail
// page.

function safeParseArray<T>(raw: string): T[] {
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    return [];
  }
}

type RetailerHealthRow = { retailer_id: string; order_count: number; net_revenue: number; retailer_health_score: number };
type RetailerDiffRow = {
  retailer_id: string;
  net_revenue_actual: number;
  net_revenue_counterfactual: number;
  net_revenue_delta: number;
};

function ScenarioDetail({ scenario }: { scenario: Scenario }) {
  const retailerHealth = safeParseArray<RetailerHealthRow>(scenario.predicted_retailer_health);

  return (
    <div className="space-y-4">
      <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
        Scenario {scenario.scenario_id} - {scenario.scenario_type.replace(/_/g, " ")} ({scenario.ticks} tick(s), completed{" "}
        {scenario.completed_at})
      </p>
      <SimpleBarChart
        title="Baseline vs. Scenario GMV"
        points={[
          { label: "Baseline", value: scenario.predicted_gmv_baseline },
          { label: "Scenario", value: scenario.predicted_gmv_scenario }
        ]}
        color="#70406f"
      />
      {retailerHealth.length > 0 ? (
        <TablePanel title="Post-scenario Retailer Health">
          <table className="min-w-full">
            <thead className="table-head">
              <tr>
                {["Retailer", "Orders", "Net Revenue", "Health Score"].map((heading) => (
                  <th key={heading} className="px-3 py-2">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {retailerHealth.map((row) => (
                <tr key={row.retailer_id}>
                  <td className="table-cell font-medium">{row.retailer_id}</td>
                  <td className="table-cell">{number(row.order_count)}</td>
                  <td className="table-cell">{money(row.net_revenue)}</td>
                  <td className="table-cell">{number(row.retailer_health_score, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TablePanel>
      ) : null}
    </div>
  );
}

function CounterfactualDetail({ counterfactual }: { counterfactual: Counterfactual }) {
  const retailerDiffs = safeParseArray<RetailerDiffRow>(counterfactual.retailer_diffs);

  return (
    <div className="space-y-4">
      <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
        Counterfactual {counterfactual.counterfactual_id} - {counterfactual.counterfactual_type.replace(/_/g, " ")} (
        {counterfactual.replay_ticks} replay tick(s), completed {counterfactual.completed_at})
      </p>
      <SimpleBarChart
        title="Actual vs. Counterfactual GMV"
        points={[
          { label: "Actual", value: counterfactual.actual_gmv },
          { label: "Counterfactual", value: counterfactual.counterfactual_gmv }
        ]}
        color="#d89b2b"
      />
      {retailerDiffs.length > 0 ? (
        <TablePanel title="Retailer Impact">
          <table className="min-w-full">
            <thead className="table-head">
              <tr>
                {["Retailer", "Actual Net Revenue", "Counterfactual Net Revenue", "Delta"].map((heading) => (
                  <th key={heading} className="px-3 py-2">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {retailerDiffs.map((row) => (
                <tr key={row.retailer_id}>
                  <td className="table-cell font-medium">{row.retailer_id}</td>
                  <td className="table-cell">{money(row.net_revenue_actual)}</td>
                  <td className="table-cell">{money(row.net_revenue_counterfactual)}</td>
                  <td className={`table-cell ${row.net_revenue_delta >= 0 ? "text-mint" : "text-coral"}`}>
                    {row.net_revenue_delta >= 0 ? "+" : ""}
                    {number(row.net_revenue_delta, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TablePanel>
      ) : null}
    </div>
  );
}

export function SimulationResultCharts({
  scenario,
  counterfactual
}: {
  scenario?: Scenario | null;
  counterfactual?: Counterfactual | null;
}) {
  if (!scenario && !counterfactual) return null;
  return (
    <div className="panel space-y-4 p-6">
      {scenario ? <ScenarioDetail scenario={scenario} /> : null}
      {counterfactual ? <CounterfactualDetail counterfactual={counterfactual} /> : null}
    </div>
  );
}
