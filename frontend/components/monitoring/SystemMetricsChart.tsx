"use client";

import type { SystemMetric } from "@/lib/types";
import { SimpleBarChart } from "@/components/charts/SimpleBarChart";

// Unlike marts.compute_model_runs/elt_model_runs (one row per run, always
// replaced wholesale), monitoring.system_metrics accumulates a genuine
// history: every monitoring/metrics.py run_all_metrics() call inserts fresh
// rows with unique metric_ids rather than overwriting the previous ones -
// see that module's persist_metrics(). That means each metric_name already
// has a real time series to plot as a trend line, not just a single current
// value - this groups by (category, metric_name) and renders one
// SimpleBarChart per metric showing its most recent points in
// chronological order, reusing SimpleBarChart the same way
// components/charts/EventLagChart.tsx does for a single dimension.

const CATEGORY_LABEL: Record<string, string> = {
  ingestion: "Ingestion",
  elt: "ELT",
  compute: "Compute",
  streaming: "Streaming"
};

const CATEGORY_COLOR: Record<string, string> = {
  ingestion: "#2e7d72",
  elt: "#d89b2b",
  compute: "#70406f",
  streaming: "#d45d4c"
};

const POINTS_PER_METRIC = 12;

function shortTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function SystemMetricsChart({ rows }: { rows: SystemMetric[] }) {
  if (rows.length === 0) {
    return <div className="panel p-6 text-sm text-slate-500 dark:text-slate-400">No system metrics recorded yet.</div>;
  }

  const byCategory = new Map<string, Map<string, SystemMetric[]>>();
  for (const row of rows) {
    if (!byCategory.has(row.metric_category)) byCategory.set(row.metric_category, new Map());
    const byMetric = byCategory.get(row.metric_category)!;
    if (!byMetric.has(row.metric_name)) byMetric.set(row.metric_name, []);
    byMetric.get(row.metric_name)!.push(row);
  }

  return (
    <div className="space-y-6">
      {Array.from(byCategory.entries()).map(([category, byMetric]) => (
        <section key={category}>
          <h3 className="mb-3 text-sm font-semibold text-slate-950 dark:text-white">
            {CATEGORY_LABEL[category] ?? category}
          </h3>
          <div className="grid gap-4 lg:grid-cols-2">
            {Array.from(byMetric.entries()).map(([metricName, metricRows]) => {
              const ordered = [...metricRows]
                .sort((a, b) => a.computed_at.localeCompare(b.computed_at))
                .slice(-POINTS_PER_METRIC);
              const unit = ordered[ordered.length - 1]?.unit ?? "";
              return (
                <SimpleBarChart
                  key={metricName}
                  title={`${metricName.replace(/_/g, " ")}${unit ? ` (${unit})` : ""}`}
                  color={CATEGORY_COLOR[category] ?? "#2e7d72"}
                  points={ordered.map((row) => ({ label: shortTime(row.computed_at), value: row.metric_value }))}
                />
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
