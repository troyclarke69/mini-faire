import { SimpleBarChart } from "@/components/charts/SimpleBarChart";
import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";
import { number } from "@/lib/api";
import type { AgentPerformance } from "@/lib/types";

// Renders api/autonomy_api.py's GET /autonomy/performance -
// _compute_performance()'s per-agent-type action_count/applied_count/
// rejected_count/advisory_count/average_reward aggregate, read straight off
// each agent's own autonomy.*_actions table. Two SimpleBarChart panels
// (reusing the exact component components/simulation/DigitalTwinVisualizer.tsx
// already uses) rank agent types by total action volume and by average
// reward - "which agent is doing the most, and which is actually helping
// GMV" - plus a table with the full applied/rejected/advisory breakdown
// neither chart alone can show.

export function AgentPerformanceChart({ performance }: { performance: AgentPerformance[] }) {
  const rows = performance ?? [];

  if (rows.length === 0) {
    return (
      <EmptyState label="No agent performance recorded yet - run the agents at least once (the Run tab, or `python orchestration/agent_flow.py`)." />
    );
  }

  const byVolume = [...rows]
    .sort((a, b) => b.action_count - a.action_count)
    .map((r) => ({ label: r.agent_type.replace(/_/g, " "), value: r.action_count }));

  const byReward = [...rows]
    .sort((a, b) => (b.average_reward ?? 0) - (a.average_reward ?? 0))
    .map((r) => ({ label: r.agent_type.replace(/_/g, " "), value: r.average_reward ?? 0 }));

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <SimpleBarChart title="Actions by Agent Type" points={byVolume} color="#d89b2b" />
        <SimpleBarChart title="Average Reward by Agent Type" points={byReward} color="#2e7d72" />
      </div>
      <TablePanel title="Performance Breakdown">
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {["Agent", "Actions", "Applied", "Rejected", "Advisory", "Avg Reward"].map((heading) => (
                <th key={heading} className="px-3 py-2">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.agent_type}>
                <td className="table-cell font-medium">{r.agent_type.replace(/_/g, " ")}</td>
                <td className="table-cell">{number(r.action_count)}</td>
                <td className="table-cell text-mint">{number(r.applied_count)}</td>
                <td className="table-cell text-coral">{number(r.rejected_count)}</td>
                <td className="table-cell text-marigold">{number(r.advisory_count)}</td>
                <td className="table-cell">
                  {r.average_reward === null ? <span className="text-slate-400">n/a</span> : number(r.average_reward, 2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TablePanel>
    </div>
  );
}
