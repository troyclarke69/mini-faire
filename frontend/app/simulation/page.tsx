import { DigitalTwinVisualizer } from "@/components/simulation/DigitalTwinVisualizer";
import { SimulationLiveBar } from "@/components/simulation/SimulationLiveBar";
import { SimulationTabs } from "@/components/simulation/SimulationTabs";
import { SimulationTimeline } from "@/components/simulation/SimulationTimeline";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { api, money, number } from "@/lib/api";

export default async function SimulationOverviewPage() {
  const [state, results] = await Promise.all([api.simulationState(), api.simulationResults(undefined, 10)]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Simulation"
        subtitle="Marketplace digital twin, agent-based what-if scenarios, and counterfactual replay (PHASE8-SIMULATION.md). Run `python orchestration/simulation_flow.py` for a full batch, or build a single scenario/counterfactual from the tabs above."
      />
      <SimulationTabs />
      <SimulationLiveBar />
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <KpiCard label="Retailers" value={number(state.summary?.retailer_count)} />
        <KpiCard label="Products" value={number(state.summary?.product_count)} />
        <KpiCard label="Twin GMV" value={money(state.summary?.gmv)} />
        <KpiCard label="Open Anomalies" value={number(state.summary?.open_anomaly_count)} />
        <KpiCard label="Active Forecasts" value={number(state.summary?.active_forecast_count)} />
      </section>
      <DigitalTwinVisualizer state={state} />
      <SimulationTimeline feed={results} limit={10} />
    </div>
  );
}
