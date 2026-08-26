"use client";

import { useEffect, useState } from "react";
import type { AgentAction, AgentConflict, AgentPerformance } from "./types";

// Real-time client for api/autonomy_api.py's /autonomy/ws
// (PHASE9-AUTONOMY.md Section 8). Mirrors frontend/lib/simulationRealtime.ts's
// useSimulationUpdates() hook almost exactly - same connect/backoff/reconnect
// state machine, same reasoning for not generalizing the two into one
// parameterized hook (see that module's docstring): the topic sets differ
// (six here - one per autonomy.*_actions table plus conflicts, each riding
// alongside a freshly-recomputed `performance` snapshot - vs. two there).
//
// Used directly by autonomy pages/components (components/autonomy/
// AutonomyLiveBar.tsx) rather than being wired into the app-wide
// LiveModeProvider, same "one app-wide toggle, several selectively-opened
// sockets" convention as lib/simulationRealtime.ts/lib/mlRealtime.ts.

export type AutonomyEnvelope = {
  type: "snapshot" | "update" | "heartbeat";
  server_time: string;
  pricing?: AgentAction[];
  inventory?: AgentAction[];
  demand?: AgentAction[];
  anomalies?: AgentAction[];
  retailer_strategy?: AgentAction[];
  conflicts?: AgentConflict[];
  performance?: AgentPerformance[];
};

export type AutonomyConnectionStatus = "connecting" | "open" | "closed" | "error";

export type AutonomyTotals = {
  pricing: number;
  inventory: number;
  demand: number;
  anomalies: number;
  retailerStrategy: number;
  conflicts: number;
};

export type AutonomyRealtimeState = {
  status: AutonomyConnectionStatus;
  lastEvent: AutonomyEnvelope | null;
  lastUpdateAt: number | null;
  updatesReceived: number;
  totals: AutonomyTotals;
  latestPerformance: AgentPerformance[] | null;
};

const EMPTY_TOTALS: AutonomyTotals = {
  pricing: 0,
  inventory: 0,
  demand: 0,
  anomalies: 0,
  retailerStrategy: 0,
  conflicts: 0
};

const INITIAL_STATE: AutonomyRealtimeState = {
  status: "closed",
  lastEvent: null,
  lastUpdateAt: null,
  updatesReceived: 0,
  totals: EMPTY_TOTALS,
  latestPerformance: null
};

const MAX_BACKOFF_MS = 15000;

function autonomyWsUrl(): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  return apiUrl.replace(/^http/, "ws").replace(/\/$/, "") + "/autonomy/ws";
}

// Also used directly by components/autonomy/AgentRunTrigger.tsx to POST an
// ad-hoc /autonomy/run against api/autonomy_api.py - that endpoint needs no
// auth token (see api/autonomy_api.py's module docstring), same posture the
// WS URL above already takes, mirroring simulationApiBase()'s exact reasoning.
export function autonomyApiBase(): string {
  return (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
}

/**
 * Opens (and, while `enabled`, keeps reconnecting) a WebSocket to
 * api/autonomy_api.py's /autonomy/ws. Pass `enabled=false` to stay fully
 * disconnected - this hook never opens a socket on its own initiative.
 */
export function useAutonomyUpdates(enabled: boolean): AutonomyRealtimeState {
  const [state, setState] = useState<AutonomyRealtimeState>(INITIAL_STATE);

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
        socket = new WebSocket(autonomyWsUrl());
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
        let envelope: AutonomyEnvelope;
        try {
          envelope = JSON.parse(event.data);
        } catch {
          return;
        }
        setState((prev) => {
          const isUpdate = envelope.type === "update";
          return {
            status: "open",
            lastEvent: envelope,
            lastUpdateAt: isUpdate ? Date.now() : prev.lastUpdateAt,
            updatesReceived: isUpdate ? prev.updatesReceived + 1 : prev.updatesReceived,
            totals: isUpdate
              ? {
                  pricing: prev.totals.pricing + (envelope.pricing?.length ?? 0),
                  inventory: prev.totals.inventory + (envelope.inventory?.length ?? 0),
                  demand: prev.totals.demand + (envelope.demand?.length ?? 0),
                  anomalies: prev.totals.anomalies + (envelope.anomalies?.length ?? 0),
                  retailerStrategy: prev.totals.retailerStrategy + (envelope.retailer_strategy?.length ?? 0),
                  conflicts: prev.totals.conflicts + (envelope.conflicts?.length ?? 0)
                }
              : prev.totals,
            latestPerformance: envelope.performance ?? prev.latestPerformance
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
