"use client";

import type { EventLagSummary } from "@/lib/types";
import { SimpleBarChart } from "./SimpleBarChart";

export function EventLagChart({ rows }: { rows: EventLagSummary[] }) {
  return (
    <SimpleBarChart
      title="Event Lag Distribution"
      color="#d45d4c"
      points={rows.map((row) => ({ label: row.event_type, value: row.avg_lag_seconds }))}
    />
  );
}

