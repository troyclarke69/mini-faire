"use client";

import { useEffect, useState } from "react";
import type { AnomalyClassification, Cluster, Forecast, Recommendation } from "./types";

// Real-time client for api/ml_api.py's /ml/ws (PHASE6-ML.md Section 8). This
// deliberately mirrors frontend/lib/monitoringRealtime.ts's
// useMonitoringUpdates() hook almost exactly - same connect/backoff/
// reconnect state machine - rather than generalizing both into one
// parameterized hook, for the same reasoning that module documents: the two
// hooks' topic sets and envelope shapes differ enough (four ML topics here -
// forecasts/clusters/recommendations/anomaly_classifications - vs. four
// monitoring topics there) that sharing would need nearly as many
// parameters as it saved lines. See api/ml_api.py's docstring for the same
// reasoning on the backend side.
//
// Used directly by ML pages/components (components/ml/MLLiveBar.tsx) rather
// than being wired into the app-wide LiveModeProvider, so a /ml/ws
// connection only opens while an ML page is actually mounted - the shared
// Live Mode ON/OFF preference (useLiveMode()) still gates it, matching
// lib/monitoringRealtime.ts's convention of one app-wide toggle, several
// selectively-opened sockets.

export type MLEnvelope = {
  type: "snapshot" | "update" | "heartbeat";
  server_time: string;
  forecasts?: Forecast[];
  clusters?: Cluster[];
  recommendations?: Recommendation[];
  anomaly_classifications?: AnomalyClassification[];
};

export type MLConnectionStatus = "connecting" | "open" | "closed" | "error";

export type MLTotals = {
  forecasts: number;
  clusters: number;
  recommendations: number;
  anomalyClassifications: number;
};

export type MLState = {
  status: MLConnectionStatus;
  lastEvent: MLEnvelope | null;
  lastUpdateAt: number | null;
  updatesReceived: number;
  totals: MLTotals;
};

const EMPTY_TOTALS: MLTotals = { forecasts: 0, clusters: 0, recommendations: 0, anomalyClassifications: 0 };

const INITIAL_STATE: MLState = {
  status: "closed",
  lastEvent: null,
  lastUpdateAt: null,
  updatesReceived: 0,
  totals: EMPTY_TOTALS
};

const MAX_BACKOFF_MS = 15000;

function mlWsUrl(): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  return apiUrl.replace(/^http/, "ws").replace(/\/$/, "") + "/ml/ws";
}

/**
 * Opens (and, while `enabled`, keeps reconnecting) a WebSocket to
 * api/ml_api.py's /ml/ws. Pass `enabled=false` to stay fully disconnected -
 * this hook never opens a socket on its own initiative.
 */
export function useMLUpdates(enabled: boolean): MLState {
  const [state, setState] = useState<MLState>(INITIAL_STATE);

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
        socket = new WebSocket(mlWsUrl());
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
        let envelope: MLEnvelope;
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
                  forecasts: prev.totals.forecasts + (envelope.forecasts?.length ?? 0),
                  clusters: prev.totals.clusters + (envelope.clusters?.length ?? 0),
                  recommendations: prev.totals.recommendations + (envelope.recommendations?.length ?? 0),
                  anomalyClassifications:
                    prev.totals.anomalyClassifications + (envelope.anomaly_classifications?.length ?? 0)
                }
              : prev.totals
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
