import { ScenarioBuilder } from "@/components/simulation/ScenarioBuilder";
import { SimulationLiveBar } from "@/components/simulation/SimulationLiveBar";
import { SimulationTabs } from "@/components/simulation/SimulationTabs";
import { SimulationTimeline } from "@/components/simulation/SimulationTimeline";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";

export default async function SimulationScenariosPage() {
  const [catalog, agents, results] = await Promise.all([
    api.simulationScenarioCatalog(),
    api.simulationAgents(),
    api.simulationResults(undefined, 20)
  ]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Scenarios"
        subtitle="What-if simulator (simulation/scenario_engine.py) - every run clones the digital twin into a baseline and a scenario branch, applies the scenario, and runs both forward the same seeded ticks so the delta isolates the scenario's own effect from simulated noise."
      />
      <SimulationTabs />
      <SimulationLiveBar />
      <ScenarioBuilder catalog={catalog} agents={agents} />
      <SimulationTimeline feed={{ scenarios: results.scenarios ?? [], counterfactuals: [] }} limit={20} title="Recent Scenario Runs" />
    </div>
  );
}
