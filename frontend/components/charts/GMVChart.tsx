"use client";

import type { RetailerDaily } from "@/lib/types";
import { SimpleBarChart } from "./SimpleBarChart";

export function GMVChart({ rows }: { rows: RetailerDaily[] }) {
  return (
    <SimpleBarChart
      title="GMV Trend"
      color="#2e7d72"
      points={rows.map((row) => ({ label: row.retailer_name, value: row.gmv }))}
    />
  );
}

