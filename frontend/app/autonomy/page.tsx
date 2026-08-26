import { AgentStateVisualizer } from "@/components/autonomy/AgentStateVisualizer";
import { AgentTimeline } from "@/components/autonomy/AgentTimeline";
import { AutonomyLiveBar } from "@/components/autonomy/AutonomyLiveBar";
import { AutonomyTabs } from "@/components/autonomy/AutonomyTabs";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { api, money, number } from "@/lib/api";

export default async function AutonomyOverviewPage() {
  const [state, actions, performance] = await Promise.all([
    api.autonomyState(),
    api.autonomyActions(undefined, 20),
    api.autonomyPerformance()
  ]);

  const perf = performance.performance ?? [];
  const totalActions = perf.reduce((sum, p) => sum + (p.action_count ?? 0), 0);
  const totalApplied = perf.reduce((sum, p) => sum + (p.applied_count ?? 0), 0);
  const totalRejected = perf.reduce((sum, p) => sum + (p.rejected_count ?? 0), 0);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Autonomy"
        subtitle="Five autonomous agents (pricing, inventory, demand, anomaly response, retailer strategy) that read the live digital twin plus ML/anomaly/monitoring signals and decide, resolve conflicts, and act (PHASE9-AUTONOMY.md). Run `python orchestration/agent_flow.py`, or use the Run tab, to have them decide against the live twin."
      />
      <AutonomyTabs />
      <AutonomyLiveBar />
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <KpiCard label="Twin GMV" value={money(state.twin_summary?.gmv)} />
        <KpiCard label="Total Decisions" value={number(totalActions)} />
        <KpiCard label="Applied" value={number(totalApplied)} />
        <KpiCard label="Rejected" value={number(totalRejected)} />
        <KpiCard label="Pipeline" value={state.pipeline_healthy ? "healthy" : "degraded"} />
      </section>
      <AgentStateVisualizer state={state} />
      <AgentTimeline actions={actions.actions ?? []} limit={20} />
    </div>
  );
}
