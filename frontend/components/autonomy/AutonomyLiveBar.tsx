"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useLiveMode } from "@/components/LiveModeProvider";
import { useAutonomyUpdates } from "@/lib/autonomyRealtime";

// Autonomy-page equivalent of components/simulation/SimulationLiveBar.tsx:
// opens api/autonomy_api.py's /autonomy/ws (via useAutonomyUpdates) and
// drives its own debounced router.refresh() when a new agent decision/
// conflict arrives - PHASE9-AUTONOMY.md Section 8's "WS/SSE push for new
// decisions/conflicts/resolutions/performance", at the row-arrival
// granularity api/autonomy_api.py's module docstring documents. Reuses the
// shared Live Mode ON/OFF preference (useLiveMode()) - same one toggle for
// the whole app, this component just opens the one socket it needs while
// mounted. Uses coral (rather than simulation's marigold) as the
// "not currently open" pulse color, since marigold is this section's own
// primary accent - reusing it for both states would erase the distinction
// SimulationLiveBar's plum/marigold pairing draws.

const PUSH_REFRESH_DEBOUNCE_MS = 3000;

export function AutonomyLiveBar() {
  const { liveMode } = useLiveMode();
  const autonomy = useAutonomyUpdates(liveMode);
  const router = useRouter();
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!liveMode || autonomy.lastEvent?.type !== "update") return;
    if (timerRef.current) return;
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      router.refresh();
    }, PUSH_REFRESH_DEBOUNCE_MS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autonomy.lastEvent, liveMode]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  if (!liveMode) return null;

  const newDecisions =
    autonomy.totals.pricing +
    autonomy.totals.inventory +
    autonomy.totals.demand +
    autonomy.totals.anomalies +
    autonomy.totals.retailerStrategy;

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-md border border-marigold/30 bg-marigold/5 px-4 py-2 text-xs text-slate-600 dark:border-marigold/30 dark:bg-marigold/10 dark:text-slate-300">
      <span className="inline-flex items-center gap-1.5 font-medium text-marigold">
        <span
          className={`h-1.5 w-1.5 rounded-full ${autonomy.status === "open" ? "bg-marigold" : "bg-coral"} animate-pulse`}
          aria-hidden="true"
        />
        Live
      </span>
      <span>
        {autonomy.updatesReceived} update{autonomy.updatesReceived === 1 ? "" : "s"} received
      </span>
      <span>
        {newDecisions} new decision{newDecisions === 1 ? "" : "s"}
      </span>
      <span>
        {autonomy.totals.conflicts} new conflict{autonomy.totals.conflicts === 1 ? "" : "s"}
      </span>
      {autonomy.lastUpdateAt ? <span>Last push: {new Date(autonomy.lastUpdateAt).toLocaleTimeString()}</span> : null}
    </div>
  );
}
