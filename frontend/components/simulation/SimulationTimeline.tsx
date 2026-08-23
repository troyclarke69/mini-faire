import Link from "next/link";
import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";
import { money } from "@/lib/api";
import type { Counterfactual, Scenario, SimulationResultsFeed } from "@/lib/types";

// PHASE8-SIMULATION.md Section 7's SimulationTimeline. A timeline of RUNS
// (each scenario/counterfactual's start -> completion), not of in-run
// ticks - simulation/digital_twin.py's per-tick event_log is ephemeral,
// scoped to one in-memory run, and never makes it into simulation.
// scenario_results/counterfactual_results (see that module's docstring on
// why only the diffed end result is persisted). Merging both result kinds
// into one time-ordered list, most-recent-first, is what "at 10:32 a
// demand_shock ran (+$420 GMV), at 10:33 a retailer_outage ran (-$210 GMV)"
// actually looks like given what this app persists.

type TimelineEntry =
  | { kind: "scenario"; row: Scenario }
  | { kind: "counterfactual"; row: Counterfactual };

export function SimulationTimeline({
  feed,
  limit,
  title = "Recent Simulation Runs"
}: {
  feed: SimulationResultsFeed;
  limit?: number;
  title?: string;
}) {
  const scenarios = feed.scenarios ?? [];
  const counterfactuals = feed.counterfactuals ?? [];

  const entries: TimelineEntry[] = [
    ...scenarios.map((row): TimelineEntry => ({ kind: "scenario", row })),
    ...counterfactuals.map((row): TimelineEntry => ({ kind: "counterfactual", row }))
  ].sort((a, b) => (a.row.completed_at < b.row.completed_at ? 1 : -1));

  const visible = limit ? entries.slice(0, limit) : entries;

  return (
    <TablePanel title={title} actions={<span className="text-xs text-slate-500">{entries.length} total</span>}>
      {visible.length === 0 ? (
        <EmptyState label="No simulation runs yet - run a scenario/counterfactual above, or `python orchestration/simulation_flow.py`." />
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-800">
          {visible.map((entry) => {
            const delta = entry.kind === "scenario" ? entry.row.predicted_gmv_delta : entry.row.counterfactual_gmv_delta;
            const id = entry.kind === "scenario" ? entry.row.scenario_id : entry.row.counterfactual_id;
            const type = entry.kind === "scenario" ? entry.row.scenario_type : entry.row.counterfactual_type;
            const href = entry.kind === "scenario" ? `/simulation/results?scenario=${id}` : `/simulation/results?counterfactual=${id}`;
            return (
              <li key={`${entry.kind}-${id}`} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
                <span
                  className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
                    entry.kind === "scenario"
                      ? "border-plum/30 bg-plum/10 text-plum"
                      : "border-marigold/30 bg-marigold/10 text-marigold"
                  }`}
                >
                  {entry.kind}
                </span>
                <span className="font-medium text-slate-950 dark:text-white">{type.replace(/_/g, " ")}</span>
                <span className={delta >= 0 ? "text-mint" : "text-coral"}>
                  {delta >= 0 ? "+" : ""}
                  {money(delta)} GMV
                </span>
                <span className="text-xs text-slate-500" title={entry.row.completed_at}>
                  {entry.row.completed_at}
                </span>
                <span className="text-xs text-slate-500">{entry.row.status}</span>
                <Link href={href} className="ml-auto text-xs font-medium text-plum">
                  View detail
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </TablePanel>
  );
}
