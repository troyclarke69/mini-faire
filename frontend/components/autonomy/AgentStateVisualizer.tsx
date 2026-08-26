import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";
import { number } from "@/lib/api";
import type { AutonomyState } from "@/lib/types";

// Renders api/autonomy_api.py's GET /autonomy/state - not live in-memory
// agent state (agent instances are ephemeral, built fresh once per
// orchestration/agent_flow.py run - see agent_framework.
// BaseAutonomousAgent's docstring), but everything genuinely persistent
// about "how are the agents configured to behave, and when did each last
// run": the fixed conflict-resolution priority order (agent_flow.
// AGENT_TYPE_PRIORITY), the shared default safety constraints every action
// passes through enforce_constraints(), the current pipeline-health
// reading, and the last recorded elt_model_runs row per agent_type.

const CONSTRAINT_LABELS: Record<string, string> = {
  max_price_change_pct: "Max price change",
  min_unit_price: "Min unit price",
  max_reorder_multiplier: "Max reorder multiplier",
  max_actions_per_agent_per_run: "Max actions per run",
  max_promotion_discount: "Max promotion discount"
};

export function AgentStateVisualizer({ state }: { state: AutonomyState }) {
  const priority = state.agent_type_priority ?? [];
  const constraints = state.default_constraints;
  const lastRuns = state.last_run_by_agent_type ?? {};

  return (
    <div className="space-y-4">
      <section className="panel p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-slate-950 dark:text-white">Pipeline Health</h2>
          <span
            className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
              state.pipeline_healthy ? "border-mint/30 bg-mint/10 text-mint" : "border-coral/30 bg-coral/10 text-coral"
            }`}
          >
            {state.pipeline_healthy ? "healthy" : "degraded"}
          </span>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          No critical alert in the last hour (monitoring.alert_events) - a signal every agent&apos;s AgentContext carries,
          though none currently gate their own decisions on it.
        </p>
      </section>

      {priority.length > 0 ? (
        <section className="panel p-4">
          <h2 className="mb-3 text-sm font-semibold text-slate-950 dark:text-white">Conflict Resolution Priority</h2>
          <p className="mb-3 text-xs text-slate-500">
            When two agents propose actions for the same entity in one run, the higher-ranked agent below wins.
          </p>
          <ol className="flex flex-wrap gap-2">
            {priority.map((agentType, index) => (
              <li
                key={agentType}
                className="inline-flex items-center gap-2 rounded-full border border-marigold/30 bg-marigold/10 px-3 py-1 text-xs font-medium text-marigold"
              >
                <span className="text-slate-400">{index + 1}</span>
                {agentType.replace(/_/g, " ")}
              </li>
            ))}
          </ol>
        </section>
      ) : (
        <EmptyState label="No agent priority order returned - is api/autonomy_api.py mounted and reachable?" />
      )}

      {constraints ? (
        <TablePanel title="Default Safety Constraints">
          <table className="min-w-full">
            <tbody>
              {Object.entries(constraints).map(([key, value]) => (
                <tr key={key}>
                  <td className="table-cell font-medium">{CONSTRAINT_LABELS[key] ?? key}</td>
                  <td className="table-cell text-slate-500">{number(value, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TablePanel>
      ) : null}

      <TablePanel title="Last Run per Agent Type">
        {Object.keys(lastRuns).length === 0 ? (
          <EmptyState label="No agent has run yet - the Run tab (or `python orchestration/agent_flow.py`) records one elt_model_runs row per agent type per run." />
        ) : (
          <table className="min-w-full">
            <thead className="table-head">
              <tr>
                {["Agent", "Target Table", "Started", "Completed", "Status"].map((heading) => (
                  <th key={heading} className="px-3 py-2">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(lastRuns).map(([agentType, run]) => (
                <tr key={agentType}>
                  <td className="table-cell font-medium">{agentType.replace(/_/g, " ")}</td>
                  <td className="table-cell text-xs text-slate-500">{run.target_table}</td>
                  <td className="table-cell text-xs text-slate-500" title={run.started_at}>
                    {run.started_at}
                  </td>
                  <td className="table-cell text-xs text-slate-500" title={run.completed_at}>
                    {run.completed_at}
                  </td>
                  <td className="table-cell">
                    <span
                      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
                        run.status === "success"
                          ? "border-mint/30 bg-mint/10 text-mint"
                          : "border-coral/30 bg-coral/10 text-coral"
                      }`}
                    >
                      {run.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </TablePanel>
    </div>
  );
}
