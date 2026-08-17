"use client";

import type { ProductVelocity } from "@/lib/types";
import { SimpleBarChart } from "./SimpleBarChart";

export function VelocityChart({ rows }: { rows: ProductVelocity[] }) {
  return (
    <SimpleBarChart
      title="Velocity Trend"
      color="#d89b2b"
      points={rows.map((row) => ({
        label: row.product_name,
        value: row.inventory_velocity ?? 0
      }))}
    />
  );
}

