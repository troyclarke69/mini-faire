"use client";

import { useMemo } from "react";
import { edgeKey, LineageGraph } from "@/components/LineageGraph";
import { useLiveMode } from "@/components/LiveModeProvider";
import type { LineageEdge } from "@/lib/types";

// Client wrapper around <LineageGraph>: merges the server-fetched `edges`
// (revalidated per lib/api.ts's REVALIDATE_SECONDS, and again whenever
// LiveModeProvider triggers a router.refresh()) with whatever lineage edges
// have arrived over the WebSocket since this page mounted, and tells
// <LineageGraph> which ones to pulse - satisfies "lineage graph animates new
// edges" from PHASE4-REALTIME&STREAMING.md Section 6C. Edges pushed live
// appear immediately even in the moment before the next server refresh
// lands them in `edges` too.
export function LineageGraphLive({ edges }: { edges: LineageEdge[] }) {
  const { liveMode, recentLineageEdges } = useLiveMode();

  const merged = useMemo(() => {
    if (!liveMode || recentLineageEdges.length === 0) return edges;
    const byKey = new Map(edges.map((edge) => [edgeKey(edge), edge]));
    for (const edge of recentLineageEdges) {
      byKey.set(edgeKey(edge), edge);
    }
    return Array.from(byKey.values());
  }, [edges, liveMode, recentLineageEdges]);

  const pulseKeys = useMemo(
    () => (liveMode ? new Set(recentLineageEdges.map(edgeKey)) : new Set<string>()),
    [liveMode, recentLineageEdges]
  );

  return <LineageGraph edges={merged} pulseKeys={pulseKeys} />;
}
