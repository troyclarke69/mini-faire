"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useLiveMode } from "@/components/LiveModeProvider";
import { useSimulationUpdates } from "@/lib/simulationRealtime";

// Simulation-page equivalent of components/ml/MLLiveBar.tsx: opens
// api/simulation_api.py's /simulation/ws (via useSimulationUpdates) and
// drives its own debounced router.refresh() when a new scenario/
// counterfactual result arrives - PHASE8-SIMULATION.md Section 6/7's
// "simulation dashboards must update live" and "simulation progress"
// bullets, at the row-arrival granularity api/simulation_api.py's module
// docstring documents (no separate intra-run progress percentage exists).
// Reuses the shared Live Mode ON/OFF preference (useLiveMode()) - same one
// toggle for the whole app, this component just opens the one socket it
// needs while mounted.

const PUSH_REFRESH_DEBOUNCE_MS = 3000;

export function SimulationLiveBar() {
  const { liveMode } = useLiveMode();
  const simulation = useSimulationUpdates(liveMode);
  const router = useRouter();
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!liveMode || simulation.lastEvent?.type !== "update") return;
    if (timerRef.current) return;
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      router.refresh();
    }, PUSH_REFRESH_DEBOUNCE_MS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simulation.lastEvent, liveMode]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  if (!liveMode) return null;

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-md border border-plum/30 bg-plum/5 px-4 py-2 text-xs text-slate-600 dark:border-plum/30 dark:bg-plum/10 dark:text-slate-300">
      <span className="inline-flex items-center gap-1.5 font-medium text-plum">
        <span
          className={`h-1.5 w-1.5 rounded-full ${simulation.status === "open" ? "bg-plum" : "bg-marigold"} animate-pulse`}
          aria-hidden="true"
        />
        Live
      </span>
      <span>
        {simulation.updatesReceived} update{simulation.updatesReceived === 1 ? "" : "s"} received
      </span>
      <span>
        {simulation.totals.scenarioResults} new scenario result{simulation.totals.scenarioResults === 1 ? "" : "s"}
      </span>
      <span>
        {simulation.totals.counterfactualResults} new counterfactual result
        {simulation.totals.counterfactualResults === 1 ? "" : "s"}
      </span>
      {simulation.lastUpdateAt ? <span>Last push: {new Date(simulation.lastUpdateAt).toLocaleTimeString()}</span> : null}
    </div>
  );
}
