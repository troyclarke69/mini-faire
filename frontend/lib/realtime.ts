"use client";

import { useEffect, useState } from "react";
import type { ComputeModelRun, EltModelRun, IngestionRun, LineageEdge } from "./types";

// Real-time client (PHASE4-REALTIME&STREAMING.md Section 6A). Connects to
// api/realtime_api.py's WebSocket endpoint and exposes a single
// useRealtimeUpdates() hook. One connection is meant to be shared across the
// whole app via components/LiveModeProvider.tsx's React context, rather than
// every page/table opening its own socket.

export type RealtimeEnvelope = {
  type: "snapshot" | "update" | "heartbeat";
  server_time: string;
  ingestion_runs?: IngestionRun[];
  elt_model_runs?: EltModelRun[];
  compute_model_runs?: ComputeModelRun[];
  lineage_edges?: LineageEdge[];
};

export type RealtimeConnectionStatus = "connecting" | "open" | "closed" | "error";

export type RealtimeTotals = {
  ingestionRuns: number;
  eltModelRuns: number;
  computeModelRuns: number;
  lineageEdges: number;
};

export type RealtimeState = {
  status: RealtimeConnectionStatus;
  lastEvent: RealtimeEnvelope | null;
  lastUpdateAt: number | null;
  updatesReceived: number;
  totals: RealtimeTotals;
  /** Small rolling buffer of the most recently pushed lineage edges, used to
   * animate/highlight new edges in <LineageGraph>. */
  recentLineageEdges: LineageEdge[];
};

const EMPTY_TOTALS: RealtimeTotals = { ingestionRuns: 0, eltModelRuns: 0, computeModelRuns: 0, lineageEdges: 0 };

const INITIAL_STATE: RealtimeState = {
  status: "closed",
  lastEvent: null,
  lastUpdateAt: null,
  updatesReceived: 0,
  totals: EMPTY_TOTALS,
  recentLineageEdges: []
};

const MAX_BACKOFF_MS = 15000;
const RECENT_EDGES_LIMIT = 30;

function realtimeUrl(): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  return apiUrl.replace(/^http/, "ws").replace(/\/$/, "") + "/realtime/ws";
}

/**
 * Opens (and, while `enabled`, keeps reconnecting) a WebSocket to
 * api/realtime_api.py's /realtime/ws endpoint. Pass `enabled=false` to stay
 * fully disconnected (used when Live Mode is off) - this hook never opens a
 * socket on its own initiative when disabled.
 */
export function useRealtimeUpdates(enabled: boolean): RealtimeState {
  const [state, setState] = useState<RealtimeState>(INITIAL_STATE);

  useEffect(() => {
    if (!enabled) {
      setState((prev) => (prev.status === "closed" ? prev : { ...prev, status: "closed" }));
      return;
    }
    if (typeof window === "undefined" || typeof WebSocket === "undefined") {
      return;
    }

    let stopped = false;
    let socket: WebSocket | null = null;
    let backoff = 1000;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    function scheduleReconnect() {
      if (stopped) return;
      const delay = backoff;
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        openSocket();
      }, delay);
    }

    function openSocket() {
      if (stopped) return;
      setState((prev) => ({ ...prev, status: "connecting" }));
      try {
        socket = new WebSocket(realtimeUrl());
      } catch {
        socket = null;
        setState((prev) => ({ ...prev, status: "error" }));
        scheduleReconnect();
        return;
      }
      const activeSocket = socket;
      if (!activeSocket) return;

      activeSocket.onopen = () => {
        backoff = 1000;
        setState((prev) => ({ ...prev, status: "open" }));
      };

      activeSocket.onmessage = (event: MessageEvent<string>) => {
        let envelope: RealtimeEnvelope;
        try {
          envelope = JSON.parse(event.data);
        } catch {
          return;
        }
        setState((prev) => {
          const newEdges = envelope.lineage_edges ?? [];
          const isUpdate = envelope.type === "update";
          return {
            status: "open",
            lastEvent: envelope,
            lastUpdateAt: isUpdate ? Date.now() : prev.lastUpdateAt,
            updatesReceived: isUpdate ? prev.updatesReceived + 1 : prev.updatesReceived,
            totals: isUpdate
              ? {
                  ingestionRuns: prev.totals.ingestionRuns + (envelope.ingestion_runs?.length ?? 0),
                  eltModelRuns: prev.totals.eltModelRuns + (envelope.elt_model_runs?.length ?? 0),
                  computeModelRuns: prev.totals.computeModelRuns + (envelope.compute_model_runs?.length ?? 0),
                  lineageEdges: prev.totals.lineageEdges + newEdges.length
                }
              : prev.totals,
            recentLineageEdges: newEdges.length
              ? [...newEdges, ...prev.recentLineageEdges].slice(0, RECENT_EDGES_LIMIT)
              : prev.recentLineageEdges
          };
        });
      };

      activeSocket.onerror = () => {
        setState((prev) => ({ ...prev, status: "error" }));
      };

      activeSocket.onclose = () => {
        socket = null;
        if (!stopped) {
          setState((prev) => ({ ...prev, status: "closed" }));
          scheduleReconnect();
        }
      };
    }

    openSocket();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [enabled]);

  return state;
}
