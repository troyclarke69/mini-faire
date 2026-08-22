"use client";

import { useMemo, useState } from "react";
import type { LineageEdge } from "@/lib/types";

const colors: Record<string, string> = {
  data: "#2e7d72",
  raw: "#d89b2b",
  staging: "#70406f",
  marts: "#d45d4c",
  api: "#2563eb"
};

function kind(node: string) {
  if (node.includes("data\\")) return "data";
  if (node.startsWith("raw.")) return "raw";
  if (node.startsWith("staging.")) return "staging";
  if (node.startsWith("marts.")) return "marts";
  return "api";
}

export function edgeKey(edge: LineageEdge): string {
  return `${edge.run_id}::${edge.source_node}::${edge.target_node}::${edge.edge_type}`;
}

export function LineageGraph({
  edges,
  pulseKeys
}: {
  edges: LineageEdge[];
  /** Keys (see edgeKey()) of edges to highlight as "just arrived" - Live
   * Mode's animated-new-edges requirement (PHASE4-REALTIME&STREAMING.md
   * Section 6C). Omit/empty outside Live Mode. */
  pulseKeys?: Set<string>;
}) {
  const [selected, setSelected] = useState<LineageEdge | null>(null);
  const nodes = useMemo(() => {
    const names = Array.from(new Set(edges.flatMap((edge) => [edge.source_node, edge.target_node]))).slice(0, 18);
    return names.map((name, index) => ({
      name,
      x: 80 + (index % 3) * 260,
      y: 50 + Math.floor(index / 3) * 82
    }));
  }, [edges]);

  const position = new Map(nodes.map((node) => [node.name, node]));
  const pulsingNodeNames = new Set(
    edges
      .filter((edge) => pulseKeys?.has(edgeKey(edge)))
      .flatMap((edge) => [edge.source_node, edge.target_node])
  );

  return (
    <section className="panel p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-950 dark:text-white">Interactive Lineage Graph</h2>
        {pulseKeys && pulseKeys.size > 0 ? (
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-mint">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-mint" aria-hidden="true" />
            {pulseKeys.size} new edge{pulseKeys.size === 1 ? "" : "s"}
          </span>
        ) : null}
      </div>
      <div className="overflow-x-auto">
        <svg width="760" height="560" role="img" aria-label="Lineage graph">
          {edges.slice(0, 24).map((edge, index) => {
            const source = position.get(edge.source_node);
            const target = position.get(edge.target_node);
            if (!source || !target) return null;
            const isNew = pulseKeys?.has(edgeKey(edge)) ?? false;
            return (
              <line
                key={`${edge.run_id}-${index}`}
                x1={source.x + 90}
                y1={source.y + 18}
                x2={target.x}
                y2={target.y + 18}
                stroke={isNew ? "#2e7d72" : "#94a3b8"}
                strokeWidth={isNew ? "3" : "1.5"}
                className={isNew ? "animate-pulse" : undefined}
                onMouseEnter={() => setSelected(edge)}
              />
            );
          })}
          {nodes.map((node) => (
            <g key={node.name} transform={`translate(${node.x}, ${node.y})`} onMouseEnter={() => setSelected(null)}>
              <rect
                width="180"
                height="38"
                rx="6"
                fill={colors[kind(node.name)]}
                stroke={pulsingNodeNames.has(node.name) ? "#2e7d72" : "none"}
                strokeWidth={pulsingNodeNames.has(node.name) ? "3" : "0"}
                className={pulsingNodeNames.has(node.name) ? "animate-pulse" : undefined}
              />
              <text x="10" y="24" fill="white" fontSize="11">
                {node.name.length > 24 ? `${node.name.slice(0, 24)}...` : node.name}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <div className="mt-3 min-h-16 rounded-md bg-slate-100 p-3 text-sm text-slate-700 dark:bg-slate-900 dark:text-slate-300">
        {selected ? (
          <p>
            {selected.edge_type} for {selected.entity} via {selected.run_id}
          </p>
        ) : (
          <p>Hover an edge to inspect lineage metadata.</p>
        )}
      </div>
    </section>
  );
}

