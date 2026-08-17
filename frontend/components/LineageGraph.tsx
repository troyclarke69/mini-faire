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

export function LineageGraph({ edges }: { edges: LineageEdge[] }) {
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

  return (
    <section className="panel p-4">
      <h2 className="mb-3 text-sm font-semibold text-slate-950 dark:text-white">Interactive Lineage Graph</h2>
      <div className="overflow-x-auto">
        <svg width="760" height="560" role="img" aria-label="Lineage graph">
          {edges.slice(0, 24).map((edge, index) => {
            const source = position.get(edge.source_node);
            const target = position.get(edge.target_node);
            if (!source || !target) return null;
            return (
              <line
                key={`${edge.run_id}-${index}`}
                x1={source.x + 90}
                y1={source.y + 18}
                x2={target.x}
                y2={target.y + 18}
                stroke="#94a3b8"
                strokeWidth="1.5"
                onMouseEnter={() => setSelected(edge)}
              />
            );
          })}
          {nodes.map((node) => (
            <g key={node.name} transform={`translate(${node.x}, ${node.y})`} onMouseEnter={() => setSelected(null)}>
              <rect width="180" height="38" rx="6" fill={colors[kind(node.name)]} />
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

