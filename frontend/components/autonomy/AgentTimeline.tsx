import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";
import { money } from "@/lib/api";
import type { AgentAction } from "@/lib/types";

// A chronological feed of agent decisions across all five agent types,
// most-recent-first - api/autonomy_api.py's GET /autonomy/actions already
// returns them pre-merged and pre-sorted by created_at (see
// _read_all_actions()'s docstring for why that's a Python merge, not a SQL
// UNION), so this component's only job is to render that order as a
// scannable list - the autonomy-section counterpart to components/
// simulation/SimulationTimeline.tsx's run-by-run feed, just one line per
// individual decision here instead of one line per completed run, since a
// single agent_flow run can itself resolve into many decisions.

const AGENT_TYPE_CLASSES: Record<string, string> = {
  pricing: "border-plum/30 bg-plum/10 text-plum",
  inventory: "border-mint/30 bg-mint/10 text-mint",
  demand: "border-marigold/30 bg-marigold/10 text-marigold",
  anomaly_response: "border-coral/30 bg-coral/10 text-coral",
  retailer_strategy: "border-slate-300 bg-slate-100 text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
};

export function AgentTimeline({
  actions,
  limit,
  title = "Recent Agent Decisions"
}: {
  actions: AgentAction[];
  limit?: number;
  title?: string;
}) {
  const visible = limit ? actions.slice(0, limit) : actions;

  return (
    <TablePanel title={title} actions={<span className="text-xs text-slate-500">{actions.length} total</span>}>
      {visible.length === 0 ? (
        <EmptyState label="No agent decisions yet - run the agents from the Run tab, or `python orchestration/agent_flow.py`." />
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-800">
          {visible.map((action) => (
            <li key={action.action_id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
              <span
                className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
                  AGENT_TYPE_CLASSES[action.agent_type] ?? AGENT_TYPE_CLASSES.retailer_strategy
                }`}
              >
                {action.agent_type.replace(/_/g, " ")}
              </span>
              <span className="font-medium text-slate-950 dark:text-white">{action.action_type.replace(/_/g, " ")}</span>
              <span className="text-xs text-slate-500">
                {action.entity_type}:{action.entity_id}
              </span>
              <span className="text-xs text-slate-500">{action.status}</span>
              {action.reward !== null ? (
                <span className={action.reward >= 0 ? "text-mint" : "text-coral"}>
                  {action.reward >= 0 ? "+" : ""}
                  {money(action.reward)}
                </span>
              ) : null}
              <span className="ml-auto text-xs text-slate-500" title={action.created_at}>
                {action.created_at}
              </span>
            </li>
          ))}
        </ul>
      )}
    </TablePanel>
  );
}
