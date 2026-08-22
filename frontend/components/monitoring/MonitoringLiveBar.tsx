"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useLiveMode } from "@/components/LiveModeProvider";
import { useMonitoringUpdates } from "@/lib/monitoringRealtime";

// Monitoring-page equivalent of components/LiveMetaBar.tsx, but self-contained:
// LiveMetaBar reads its data from the app-wide LiveModeProvider context
// (whose socket only carries ingestion/elt/compute/lineage topics), so
// monitoring pages need their own small live bar that both opens
// /monitoring/ws (via useMonitoringUpdates) AND drives its own debounced
// router.refresh() - LiveModeProvider's refresh trigger never fires for
// monitoring topics, since it never subscribes to them.
//
// Reuses the app-wide Live Mode ON/OFF preference (useLiveMode()) rather
// than adding a second toggle - one on/off switch for the whole app, with
// each page opening only the socket(s) it actually needs.

const PUSH_REFRESH_DEBOUNCE_MS = 3000;

export function MonitoringLiveBar() {
  const { liveMode } = useLiveMode();
  const monitoring = useMonitoringUpdates(liveMode);
  const router = useRouter();
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!liveMode || monitoring.lastEvent?.type !== "update") return;
    if (timerRef.current) return;
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      router.refresh();
    }, PUSH_REFRESH_DEBOUNCE_MS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [monitoring.lastEvent, liveMode]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  if (!liveMode) return null;

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-md border border-mint/30 bg-mint/5 px-4 py-2 text-xs text-slate-600 dark:border-mint/30 dark:bg-mint/10 dark:text-slate-300">
      <span className="inline-flex items-center gap-1.5 font-medium text-mint">
        <span
          className={`h-1.5 w-1.5 rounded-full ${monitoring.status === "open" ? "bg-mint" : "bg-marigold"} animate-pulse`}
          aria-hidden="true"
        />
        Live
      </span>
      <span>
        {monitoring.updatesReceived} update{monitoring.updatesReceived === 1 ? "" : "s"} received
      </span>
      <span>
        {monitoring.totals.anomalies} new anomal{monitoring.totals.anomalies === 1 ? "y" : "ies"}
      </span>
      <span>
        {monitoring.totals.alerts} new alert{monitoring.totals.alerts === 1 ? "" : "s"}
      </span>
      <span>
        {monitoring.totals.schemaDrift} new drift event{monitoring.totals.schemaDrift === 1 ? "" : "s"}
      </span>
      {monitoring.lastUpdateAt ? <span>Last push: {new Date(monitoring.lastUpdateAt).toLocaleTimeString()}</span> : null}
    </div>
  );
}
