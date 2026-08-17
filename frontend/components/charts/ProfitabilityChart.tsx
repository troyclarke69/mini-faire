"use client";

import type { OrderProfitability } from "@/lib/types";
import { SimpleBarChart } from "./SimpleBarChart";

export function ProfitabilityChart({ rows }: { rows: OrderProfitability[] }) {
  return (
    <SimpleBarChart
      title="Profitability Trend"
      color="#70406f"
      points={rows.map((row) => ({ label: row.order_id, value: row.estimated_profit }))}
    />
  );
}

