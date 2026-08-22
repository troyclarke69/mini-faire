"use client";

import { useLiveMode } from "@/components/LiveModeProvider";

// PHASE4-REALTIME&STREAMING.md Section 6B: "show live ingestion counters"
// and "show live compute run timestamps" on each of the six live pages.
// Renders nothing when Live Mode is off, so it's a no-op to drop into any
// page.
export function LiveMetaBar() {
  const { liveMode, status, updatesReceived, totals, lastUpdateAt, lastEvent } = useLiveMode();

  if (!liveMode) return null;

  const lastComputeRun = lastEvent?.compute_model_runs?.[0];

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-md border border-mint/30 bg-mint/5 px-4 py-2 text-xs text-slate-600 dark:border-mint/30 dark:bg-mint/10 dark:text-slate-300">
      <span className="inline-flex items-center gap-1.5 font-medium text-mint">
        <span
          className={`h-1.5 w-1.5 rounded-full ${status === "open" ? "bg-mint" : "bg-marigold"} animate-pulse`}
          aria-hidden="true"
        />
        Live
      </span>
      <span>
        {updatesReceived} update{updatesReceived === 1 ? "" : "s"} received
      </span>
      <span>
        {totals.ingestionRuns} new ingestion run{totals.ingestionRuns === 1 ? "" : "s"}
      </span>
      <span>
        {totals.computeModelRuns} new compute run{totals.computeModelRuns === 1 ? "" : "s"}
      </span>
      {lastComputeRun ? <span>Last compute run: {lastComputeRun.model_name} @ {lastComputeRun.computed_at}</span> : null}
      {lastUpdateAt ? <span>Last push: {new Date(lastUpdateAt).toLocaleTimeString()}</span> : null}
    </div>
  );
}
