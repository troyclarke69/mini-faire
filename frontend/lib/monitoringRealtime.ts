"use client";

import { useEffect, useState } from "react";
import type { AlertEvent, AnomalyEvent, SchemaDriftEvent, SystemMetric } from "./types";

// Real-time client for api/monitoring_api.py's /monitoring/ws (PHASE5-MONITORING.md
// Section 5). This deliberately mirrors frontend/lib/realtime.ts's
// useRealtimeUpdates() hook almost exactly - same connect/backoff/reconnect
// state machine - rather than generalizing both into one parameterized hook.
// The two hooks' envelope shapes and topic sets are different enough (four
// monitoring topics here vs. four pipeline topics there, no shared "totals"
// concept) that a shared abstraction would need nearly as many parameters as
// it saved lines, for two call sites. See api/monitoring_api.py's docstring
// for the same reasoning on the backend side.
//
// Used directly by monitoring pages/components (components/monitoring/
// MonitoringLiveBar.tsx) rather than being wired into the app-wide
// LiveModeProvider, so a /monitoring/ws connection only opens while a
// monitoring page is actually mounted - the shared Live Mode ON/OFF
// preference (useLiveMode()) still gates it, so there's one toggle for the
// whole app, just two sockets opened selectively based on what's on screen.

export type MonitoringEnvelope = {
  type: "snapshot" | "update" | "heartbeat";
  server_time: string;
  anomalies?: AnomalyEvent[];
  alerts?: AlertEvent[];
  system_metrics?: SystemMetric[];
  schema_drift?: SchemaDriftEvent[];
};

export type MonitoringConnectionStatus = "connecting" | "open" | "closed" | "error";

export type MonitoringTotals = {
  anomalies: number;
  alerts: number;
  systemMetrics: number;
  schemaDrift: number;
};

export type MonitoringState = {
  status: MonitoringConnectionStatus;
  lastEvent: MonitoringEnvelope | null;
  lastUpdateAt: number | null;
  updatesReceived: number;
  totals: MonitoringTotals;
};

const EMPTY_TOTALS: MonitoringTotals = { anomalies: 0, alerts: 0, systemMetrics: 0, schemaDrift: 0 };

const INITIAL_STATE: MonitoringState = {
  status: "closed",
  lastEvent: null,
  lastUpdateAt: null,
  updatesReceived: 0,
  totals: EMPTY_TOTALS
};

const MAX_BACKOFF_MS = 15000;

function monitoringUrl(): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  return apiUrl.replace(/^http/, "ws").replace(/\/$/, "") + "/monitoring/ws";
}

/**
 * Opens (and, while `enabled`, keeps reconnecting) a WebSocket to
 * api/monitoring_api.py's /monitoring/ws. Pass `enabled=false` to stay fully
 * disconnected - this hook never opens a socket on its own initiative.
 */
export function useMonitoringUpdates(enabled: boolean): MonitoringState {
  const [state, setState] = useState<MonitoringState>(INITIAL_STATE);

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
        socket = new WebSocket(monitoringUrl());
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
        let envelope: MonitoringEnvelope;
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
                  anomalies: prev.totals.anomalies + (envelope.anomalies?.length ?? 0),
                  alerts: prev.totals.alerts + (envelope.alerts?.length ?? 0),
                  systemMetrics: prev.totals.systemMetrics + (envelope.system_metrics?.length ?? 0),
                  schemaDrift: prev.totals.schemaDrift + (envelope.schema_drift?.length ?? 0)
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
