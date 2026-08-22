"use client";

import type { Cluster } from "@/lib/types";

// Renders one entity type's cluster assignments (already filtered by the
// caller to entity_type='retailer' or 'product' - see
// app/ml/clusters/page.tsx) as an SVG scatter plot using ml/models/
// clustering.py's PCA-reduced `plot_x`/`plot_y` coordinates - a real 2D
// projection of the standardized feature space, not an arbitrary layout, so
// visual proximity on this map reflects actual feature similarity. Points
// are colored by `segment_name` (the trait-axis label ml/models/
// clustering.py's `_label_cluster()` assigns per cluster - see that
// module's docstring) rather than by the raw numeric `cluster_label`, since
// segment_name is what's meaningful to a reader.

const WIDTH = 560;
const HEIGHT = 360;
const MARGIN = 24;
const PALETTE = ["#2e7d72", "#d45d4c", "#d89b2b", "#70406f", "#3b6fa0", "#8a8a3c", "#a05a8a", "#4c9a7a"];

function colorFor(segmentName: string, palette: Map<string, string>): string {
  if (segmentName === "outlier") return "#94a3b8"; // slate-400 - always neutral, regardless of palette assignment order
  if (!palette.has(segmentName)) {
    palette.set(segmentName, PALETTE[palette.size % PALETTE.length]);
  }
  return palette.get(segmentName)!;
}

export function ClusterMap({ title, rows }: { title: string; rows: Cluster[] }) {
  if (rows.length === 0) {
    return <div className="panel p-6 text-sm text-slate-500 dark:text-slate-400">No cluster assignments for this entity type yet.</div>;
  }

  const xs = rows.map((row) => row.plot_x);
  const ys = rows.map((row) => row.plot_y);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMin = Math.min(...ys);
  const yMax = Math.max(...ys);
  const xSpan = xMax - xMin || 1;
  const ySpan = yMax - yMin || 1;

  const plotX = (v: number) => MARGIN + ((v - xMin) / xSpan) * (WIDTH - 2 * MARGIN);
  const plotY = (v: number) => HEIGHT - MARGIN - ((v - yMin) / ySpan) * (HEIGHT - 2 * MARGIN);

  const palette = new Map<string, string>();
  // Assign colors in a stable order (first appearance in the sorted-by-name
  // segment list) so the same segment gets the same color across re-renders
  // regardless of row order.
  const segmentNames = Array.from(new Set(rows.map((row) => row.segment_name))).sort();
  for (const name of segmentNames) colorFor(name, palette);

  return (
    <section className="panel p-4">
      <h2 className="mb-2 text-sm font-semibold text-slate-950 dark:text-white">{title}</h2>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" role="img" aria-label={`${title} cluster map`}>
        {rows.map((row) => (
          <circle
            key={row.cluster_id}
            cx={plotX(row.plot_x)}
            cy={plotY(row.plot_y)}
            r={4}
            fill={colorFor(row.segment_name, palette)}
            fillOpacity={0.8}
          >
            <title>
              {row.entity_id}: {row.segment_name.replace(/_/g, " ")}
            </title>
          </circle>
        ))}
      </svg>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600 dark:text-slate-300">
        {segmentNames.map((name) => (
          <span key={name} className="inline-flex items-center gap-1.5">
            <span
              className="h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: name === "outlier" ? "#94a3b8" : palette.get(name) }}
              aria-hidden="true"
            />
            {name.replace(/_/g, " ")} ({rows.filter((row) => row.segment_name === name).length})
          </span>
        ))}
      </div>
    </section>
  );
}
