import { AgentPerformanceChart } from "@/components/autonomy/AgentPerformanceChart";
import { AutonomyLiveBar } from "@/components/autonomy/AutonomyLiveBar";
import { AutonomyTabs } from "@/components/autonomy/AutonomyTabs";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";

export default async function AutonomyPerformancePage() {
  const feed = await api.autonomyPerformance();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Performance"
        subtitle="Per-agent-type action volume, applied/rejected/advisory breakdown, and average reward (run-level baseline-projection GMV delta - see autonomy/agent_framework.py's score_reward() docstring), read straight off each agent's own autonomy.*_actions table."
      />
      <AutonomyTabs />
      <AutonomyLiveBar />
      <AgentPerformanceChart performance={feed.performance ?? []} />
    </div>
  );
}
