"use client";

import { Radio } from "lucide-react";
import { useLiveMode } from "@/components/LiveModeProvider";

const STATUS_LABEL: Record<string, string> = {
  connecting: "Connecting",
  open: "ON",
  closed: "OFF",
  error: "Reconnecting"
};

export function LiveModeToggle() {
  const { liveMode, setLiveMode, status, updatesReceived } = useLiveMode();

  const dotClass = !liveMode ? "bg-slate-400" : status === "open" ? "bg-mint animate-pulse" : "bg-marigold animate-pulse";

  const label = liveMode ? STATUS_LABEL[status] ?? "ON" : "OFF";

  return (
    <button
      type="button"
      onClick={() => setLiveMode(!liveMode)}
      aria-pressed={liveMode}
      title={liveMode ? `Live Mode on - ${updatesReceived} update(s) received this session` : "Live Mode off - click to enable auto-refreshing data"}
      className={`inline-flex h-10 items-center gap-2 rounded-md border px-3 text-sm font-medium transition ${
        liveMode
          ? "border-mint bg-mint/10 text-mint dark:border-mint dark:bg-mint/10 dark:text-mint"
          : "border-slate-300 bg-white text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
      }`}
    >
      <span className={`h-2 w-2 rounded-full ${dotClass}`} aria-hidden="true" />
      <Radio className="h-4 w-4" aria-hidden="true" />
      <span>Live Mode: {label}</span>
    </button>
  );
}
