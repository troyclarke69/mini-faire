import { AgentStrategyEditor } from "@/components/simulation/AgentStrategyEditor";
import { SimulationLiveBar } from "@/components/simulation/SimulationLiveBar";
import { SimulationTabs } from "@/components/simulation/SimulationTabs";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";

export default async function SimulationAgentsPage() {
  const agents = await api.simulationAgents();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Agents"
        subtitle="Agent-based modeling layer (simulation/agents/) - one marketplace agent, one retailer agent per retailer, one product agent per product. Strategies shown here are the defaults every agent starts from; override them for a single scenario run from the Scenarios tab."
      />
      <SimulationTabs />
      <SimulationLiveBar />
      <AgentStrategyEditor agents={agents} />
    </div>
  );
}
