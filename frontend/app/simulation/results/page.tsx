import { SimulationLiveBar } from "@/components/simulation/SimulationLiveBar";
import { SimulationResultCharts } from "@/components/simulation/SimulationResultCharts";
import { SimulationTabs } from "@/components/simulation/SimulationTabs";
import { SimulationTimeline } from "@/components/simulation/SimulationTimeline";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";

export default async function SimulationResultsPage({
  searchParams
}: {
  searchParams: { scenario?: string; counterfactual?: string };
}) {
  const scenarioId = typeof searchParams.scenario === "string" ? searchParams.scenario : undefined;
  const counterfactualId = typeof searchParams.counterfactual === "string" ? searchParams.counterfactual : undefined;

  const [results, scenarioDetail, counterfactualDetail] = await Promise.all([
    api.simulationResults(undefined, 100),
    scenarioId ? api.simulationScenarioDetail(scenarioId) : Promise.resolve(null),
    counterfactualId ? api.simulationCounterfactualDetail(counterfactualId) : Promise.resolve(null)
  ]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Results"
        subtitle="Every persisted scenario/counterfactual run (simulation.scenario_results / simulation.counterfactual_results) - select one from the timeline below (or a shared /simulation/results?scenario=... link) to see its full detail."
      />
      <SimulationTabs />
      <SimulationLiveBar />
      {scenarioId && !scenarioDetail ? (
        <p className="text-sm text-coral">No scenario result found for id {scenarioId}.</p>
      ) : null}
      {counterfactualId && !counterfactualDetail ? (
        <p className="text-sm text-coral">No counterfactual result found for id {counterfactualId}.</p>
      ) : null}
      <SimulationResultCharts scenario={scenarioDetail} counterfactual={counterfactualDetail} />
      <SimulationTimeline feed={results} limit={100} />
    </div>
  );
}
