"use client";

import { CalendarDays } from "lucide-react";

export function DateRangePicker({
  start = "",
  end = "",
  onChange
}: {
  start?: string;
  end?: string;
  onChange?: (range: { start: string; end: string }) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <CalendarDays className="h-4 w-4 text-slate-500" />
      <input
        type="date"
        value={start}
        onChange={(event) => onChange?.({ start: event.target.value, end })}
        className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
        aria-label="Start date"
      />
      <input
        type="date"
        value={end}
        onChange={(event) => onChange?.({ start, end: event.target.value })}
        className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
        aria-label="End date"
      />
    </div>
  );
}

