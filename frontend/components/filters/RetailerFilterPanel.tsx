"use client";

import { useMemo, useState } from "react";
import type { RetailerDaily } from "@/lib/types";
import { GMVChart } from "@/components/charts/GMVChart";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import { RetailerSelector } from "@/components/filters/RetailerSelector";

export function RetailerFilterPanel({ rows }: { rows: RetailerDaily[] }) {
  const [retailer, setRetailer] = useState("");
  const [range, setRange] = useState({ start: "", end: "" });

  const retailers = useMemo(
    () => Array.from(new Set(rows.map((row) => row.retailer_name))).sort(),
    [rows]
  );

  const filtered = useMemo(() => {
    return rows.filter((row) => {
      if (retailer && row.retailer_name !== retailer) return false;
      if (range.start && row.order_date < range.start) return false;
      if (range.end && row.order_date > range.end) return false;
      return true;
    });
  }, [rows, retailer, range]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <DateRangePicker start={range.start} end={range.end} onChange={setRange} />
        <RetailerSelector retailers={retailers} value={retailer} onChange={setRetailer} />
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {filtered.length} of {rows.length} rows match the current filters
        </span>
      </div>
      <GMVChart rows={filtered} />
    </div>
  );
}
