import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";
import { money, number } from "@/lib/api";
import type { AgentAction } from "@/lib/types";

// Renders the shared row shape every autonomy.*_actions table persists
// (autonomy/agent_framework.py's AgentAction - see api/autonomy_api.py's
// GET /autonomy/actions and its five per-agent-type siblings) - one table
// component for both the combined feed and every single-agent-type page,
// same "one table component driven by a shared row shape" convention this
// repo's other per-domain tables already use. `params`/full `rationale` are
// opened per-row via <details> rather than their own columns - params is a
// JSON blob whose shape varies by action_type (see AgentAction's own
// docstring), so a fixed column set can't render it meaningfully, and a full
// rationale sentence is too long for a table cell.

const STATUS_CLASSES: Record<string, string> = {
  applied: "border-mint/30 bg-mint/10 text-mint",
  proposed: "border-marigold/30 bg-marigold/10 text-marigold",
  rejected: "border-coral/30 bg-coral/10 text-coral",
  reverted: "border-slate-300 bg-slate-100 text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
};

function statusBadgeClasses(status: string): string {
  return `inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
    STATUS_CLASSES[status] ?? STATUS_CLASSES.reverted
  }`;
}

function formatParams(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

export function AgentDecisionTable({
  actions,
  title = "Agent Decisions",
  showAgentType = true
}: {
  actions: AgentAction[];
  title?: string;
  showAgentType?: boolean;
}) {
  const headings = [...(showAgentType ? ["Agent"] : []), "Action", "Entity", "Status", "Confidence", "Reward", "Rationale", "Created"];

  return (
    <TablePanel title={title} actions={<span className="text-xs text-slate-500">{actions.length} total</span>}>
      {actions.length === 0 ? (
        <EmptyState label="No agent decisions yet - POST /autonomy/run (or the Run tab) to have the five agents decide against the live twin." />
      ) : (
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {headings.map((heading) => (
                <th key={heading} className="px-3 py-2">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {actions.map((action) => (
              <tr key={action.action_id}>
                {showAgentType ? <td className="table-cell font-medium">{action.agent_type.replace(/_/g, " ")}</td> : null}
                <td className="table-cell">{action.action_type.replace(/_/g, " ")}</td>
                <td className="table-cell text-xs text-slate-500">
                  {action.entity_type}:{action.entity_id}
                </td>
                <td className="table-cell">
                  <span className={statusBadgeClasses(action.status)}>{action.status}</span>
                </td>
                <td className="table-cell text-xs text-slate-500">{number(action.confidence, 2)}</td>
                <td className="table-cell text-xs">
                  {action.reward === null ? (
                    <span className="text-slate-400">n/a</span>
                  ) : (
                    <span className={action.reward >= 0 ? "text-mint" : "text-coral"}>
                      {action.reward >= 0 ? "+" : ""}
                      {money(action.reward)}
                    </span>
                  )}
                </td>
                <td className="table-cell max-w-xs text-xs text-slate-500">
                  <details>
                    <summary className="cursor-pointer truncate">{action.rationale}</summary>
                    <pre className="mt-1 max-w-xs whitespace-pre-wrap break-words text-[11px] text-slate-400">
                      {formatParams(action.params)}
                    </pre>
                  </details>
                </td>
                <td className="table-cell text-xs text-slate-500" title={action.created_at}>
                  {action.created_at}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </TablePanel>
  );
}
