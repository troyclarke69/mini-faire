"use client";

import { useMemo, useState } from "react";
import type { ProductVelocity } from "@/lib/types";
import { VelocityChart } from "@/components/charts/VelocityChart";
import { ProductSelector } from "@/components/filters/ProductSelector";

export function ProductFilterPanel({ rows }: { rows: ProductVelocity[] }) {
  const [product, setProduct] = useState("");

  const products = useMemo(
    () => Array.from(new Set(rows.map((row) => row.product_name))).sort(),
    [rows]
  );

  const filtered = useMemo(
    () => (product ? rows.filter((row) => row.product_name === product) : rows),
    [rows, product]
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <ProductSelector products={products} value={product} onChange={setProduct} />
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {filtered.length} of {rows.length} rows match the current filter
        </span>
      </div>
      <VelocityChart rows={filtered} />
    </div>
  );
}
