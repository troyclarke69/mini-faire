"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useLiveMode } from "@/components/LiveModeProvider";
import { useMLUpdates } from "@/lib/mlRealtime";

// ML-page equivalent of components/monitoring/MonitoringLiveBar.tsx: opens
// api/ml_api.py's /ml/ws (via useMLUpdates) and drives its own debounced
// router.refresh() when new forecasts/clusters/recommendations/anomaly
// classifications arrive, since the app-wide LiveModeProvider's socket only
// carries ingestion/elt/compute/lineage topics and never subscribes to ML
// ones. Reuses the shared Live Mode ON/OFF preference (useLiveMode()) -
// same one toggle for the whole app, this page just opens the one socket it
// needs while mounted.

const PUSH_REFRESH_DEBOUNCE_MS = 3000;

export function MLLiveBar() {
  const { liveMode } = useLiveMode();
  const ml = useMLUpdates(liveMode);
  const router = useRouter();
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!liveMode || ml.lastEvent?.type !== "update") return;
    if (timerRef.current) return;
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      router.refresh();
    }, PUSH_REFRESH_DEBOUNCE_MS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ml.lastEvent, liveMode]);

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
          className={`h-1.5 w-1.5 rounded-full ${ml.status === "open" ? "bg-plum" : "bg-marigold"} animate-pulse`}
          aria-hidden="true"
        />
        Live
      </span>
      <span>
        {ml.updatesReceived} update{ml.updatesReceived === 1 ? "" : "s"} received
      </span>
      <span>
        {ml.totals.forecasts} new forecast{ml.totals.forecasts === 1 ? "" : "s"}
      </span>
      <span>
        {ml.totals.clusters} new cluster assignment{ml.totals.clusters === 1 ? "" : "s"}
      </span>
      <span>
        {ml.totals.recommendations} new recommendation{ml.totals.recommendations === 1 ? "" : "s"}
      </span>
      <span>
        {ml.totals.anomalyClassifications} new classification{ml.totals.anomalyClassifications === 1 ? "" : "s"}
      </span>
      {ml.lastUpdateAt ? <span>Last push: {new Date(ml.lastUpdateAt).toLocaleTimeString()}</span> : null}
    </div>
  );
}
