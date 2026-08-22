"use client";

import type { RetailerDaily } from "@/lib/types";
import { SimpleBarChart } from "./SimpleBarChart";

export function GMVChart({ rows }: { rows: RetailerDaily[] }) {
  const byDate = new Map<string, number>();
  for (const row of rows) {
    byDate.set(row.order_date, (byDate.get(row.order_date) ?? 0) + row.gmv);
  }
  const points = Array.from(byDate.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([order_date, gmv]) => ({ label: order_date, value: gmv }));

  return <SimpleBarChart title="GMV Trend (multi-day)" color="#2e7d72" points={points} />;
}

