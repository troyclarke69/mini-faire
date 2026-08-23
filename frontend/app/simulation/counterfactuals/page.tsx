import { CounterfactualBuilder } from "@/components/simulation/CounterfactualBuilder";
import { SimulationLiveBar } from "@/components/simulation/SimulationLiveBar";
import { SimulationTabs } from "@/components/simulation/SimulationTabs";
import { SimulationTimeline } from "@/components/simulation/SimulationTimeline";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";

export default async function SimulationCounterfactualsPage() {
  const [catalog, results] = await Promise.all([api.simulationCounterfactualCatalog(), api.simulationResults(undefined, 20)]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Counterfactuals"
        subtitle="Replay engine (simulation/counterfactuals.py) - removes or modifies real historical orders, recomputes the same order-health metrics compute/polars/transform_orders.py already uses, then replays the ABM agents forward from the point of divergence to see whether the effect compounds, fades, or reverses."
      />
      <SimulationTabs />
      <SimulationLiveBar />
      <CounterfactualBuilder catalog={catalog} />
      <SimulationTimeline
        feed={{ scenarios: [], counterfactuals: results.counterfactuals ?? [] }}
        limit={20}
        title="Recent Counterfactual Runs"
      />
    </div>
  );
}
