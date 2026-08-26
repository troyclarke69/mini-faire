import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";
import type { AgentConflict } from "@/lib/types";

// Renders autonomy.conflicts rows (orchestration/agent_flow.py's
// _resolve_and_apply() - see that function's module docstring for the fixed
// AGENT_TYPE_PRIORITY order every row here is a real instance of) - one row
// per entity-collision the run resolved, winner and rejected side by side
// so "why did pricing's price cut get dropped here" is answerable at a
// glance without cross-referencing the decision tables. Also reused inline
// by components/autonomy/AgentRunTrigger.tsx for the freshly-resolved
// conflicts a POST /autonomy/run response carries (same shape, just not yet
// round-tripped through the table).

export function AgentConflictViewer({
  conflicts,
  title = "Conflict Resolutions"
}: {
  conflicts: AgentConflict[];
  title?: string;
}) {
  return (
    <TablePanel title={title} actions={<span className="text-xs text-slate-500">{conflicts.length} total</span>}>
      {conflicts.length === 0 ? (
        <EmptyState label="No conflicts recorded yet - a conflict only happens when two agents propose actions for the same entity in the same run." />
      ) : (
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {["Entity", "Winner", "Rejected", "Resolved"].map((heading) => (
                <th key={heading} className="px-3 py-2">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {conflicts.map((conflict) => (
              <tr key={conflict.conflict_id}>
                <td className="table-cell text-xs text-slate-500">
                  {conflict.entity_type}:{conflict.entity_id}
                </td>
                <td className="table-cell">
                  <span className="inline-flex items-center rounded-full border border-mint/30 bg-mint/10 px-2 py-0.5 text-xs font-medium text-mint">
                    {conflict.winning_agent_type ? conflict.winning_agent_type.replace(/_/g, " ") : "n/a"}
                  </span>
                  {conflict.winning_action_type ? (
                    <span className="ml-2 text-xs text-slate-500">{conflict.winning_action_type.replace(/_/g, " ")}</span>
                  ) : null}
                </td>
                <td className="table-cell">
                  <span className="inline-flex items-center rounded-full border border-coral/30 bg-coral/10 px-2 py-0.5 text-xs font-medium text-coral">
                    {conflict.rejected_agent_type.replace(/_/g, " ")}
                  </span>
                  <span className="ml-2 text-xs text-slate-500">{conflict.rejected_action_type.replace(/_/g, " ")}</span>
                </td>
                <td className="table-cell text-xs text-slate-500" title={conflict.created_at}>
                  {conflict.created_at}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </TablePanel>
  );
}
