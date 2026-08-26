import { AgentStateVisualizer } from "@/components/autonomy/AgentStateVisualizer";
import { AutonomyLiveBar } from "@/components/autonomy/AutonomyLiveBar";
import { AutonomyTabs } from "@/components/autonomy/AutonomyTabs";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";

export default async function AutonomyAgentsPage() {
  const state = await api.autonomyState();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Agents"
        subtitle="How the five autonomous agents are configured to behave - the fixed conflict-resolution priority order, the shared default safety constraints every action passes through enforce_constraints(), and when each agent type last ran (api/autonomy_api.py's GET /autonomy/state)."
      />
      <AutonomyTabs />
      <AutonomyLiveBar />
      <AgentStateVisualizer state={state} />
    </div>
  );
}
