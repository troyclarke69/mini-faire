import { AgentRunTrigger } from "@/components/autonomy/AgentRunTrigger";
import { AgentTimeline } from "@/components/autonomy/AgentTimeline";
import { AutonomyLiveBar } from "@/components/autonomy/AutonomyLiveBar";
import { AutonomyTabs } from "@/components/autonomy/AutonomyTabs";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";

export default async function AutonomyRunPage() {
  const actions = await api.autonomyActions(undefined, 20);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Run"
        subtitle="Trigger one ad-hoc orchestration/agent_flow.py pass (live/tick/scenario mode) against the current digital twin - the interactive counterpart to `python orchestration/agent_flow.py` or a scheduled external caller (PHASE9-AUTONOMY.md Section 7)."
      />
      <AutonomyTabs />
      <AutonomyLiveBar />
      <AgentRunTrigger />
      <AgentTimeline actions={actions.actions ?? []} limit={20} title="Recent Agent Decisions" />
    </div>
  );
}
