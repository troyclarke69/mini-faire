import { AgentConflictViewer } from "@/components/autonomy/AgentConflictViewer";
import { AutonomyLiveBar } from "@/components/autonomy/AutonomyLiveBar";
import { AutonomyTabs } from "@/components/autonomy/AutonomyTabs";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";

export default async function AutonomyConflictsPage() {
  const feed = await api.autonomyConflicts(100);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Conflicts"
        subtitle="Every entity-level collision orchestration/agent_flow.py's conflict resolution has resolved (autonomy.conflicts) - two agents proposed actions for the same entity in one run, and the fixed AGENT_TYPE_PRIORITY order named a winner while the loser was rejected."
      />
      <AutonomyTabs />
      <AutonomyLiveBar />
      <AgentConflictViewer conflicts={feed.conflicts ?? []} title="All Conflicts" />
    </div>
  );
}
