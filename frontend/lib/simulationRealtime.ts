"use client";

import { useEffect, useState } from "react";
import type { Counterfactual, Scenario } from "./types";

// Real-time client for api/simulation_api.py's /simulation/ws
// (PHASE8-SIMULATION.md Section 6). Deliberately mirrors frontend/lib/
// mlRealtime.ts's useMLUpdates() hook almost exactly - same connect/backoff/
// reconnect state machine, same reasoning for not generalizing the two into
// one parameterized hook (see that module's docstring and api/ml_api.py's
// docstring for why): the topic sets differ (two here - scenario_results/
// counterfactual_results - vs. four there) and a shared abstraction would
// need nearly as many parameters as it saved lines.
//
// Used directly by simulation pages/components (components/simulation/
// SimulationLiveBar.tsx) rather than being wired into the app-wide
// LiveModeProvider, same "one app-wide toggle, several selectively-opened
// sockets" convention as lib/mlRealtime.ts/lib/monitoringRealtime.ts.

export type SimulationEnvelope = {
  type: "snapshot" | "update" | "heartbeat";
  server_time: string;
  scenario_results?: Scenario[];
  counterfactual_results?: Counterfactual[];
};

export type SimulationConnectionStatus = "connecting" | "open" | "closed" | "error";

export type SimulationTotals = {
  scenarioResults: number;
  counterfactualResults: number;
};

export type SimulationRealtimeState = {
  status: SimulationConnectionStatus;
  lastEvent: SimulationEnvelope | null;
  lastUpdateAt: number | null;
  updatesReceived: number;
  totals: SimulationTotals;
};

const EMPTY_TOTALS: SimulationTotals = { scenarioResults: 0, counterfactualResults: 0 };

const INITIAL_STATE: SimulationRealtimeState = {
  status: "closed",
  lastEvent: null,
  lastUpdateAt: null,
  updatesReceived: 0,
  totals: EMPTY_TOTALS
};

const MAX_BACKOFF_MS = 15000;

function simulationWsUrl(): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  return apiUrl.replace(/^http/, "ws").replace(/\/$/, "") + "/simulation/ws";
}

// Also used directly by components/simulation/ScenarioBuilder.tsx and
// CounterfactualBuilder.tsx to POST an ad-hoc run to api/simulation_api.py -
// those endpoints need no auth token (see api/simulation_api.py's module
// docstring), so a plain client-side fetch against the FastAPI backend is
// the same posture the WS URL above already takes, not a new pattern.
export function simulationApiBase(): string {
  return (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
}

/**
 * Opens (and, while `enabled`, keeps reconnecting) a WebSocket to
 * api/simulation_api.py's /simulation/ws. Pass `enabled=false` to stay
 * fully disconnected - this hook never opens a socket on its own initiative.
 */
export function useSimulationUpdates(enabled: boolean): SimulationRealtimeState {
  const [state, setState] = useState<SimulationRealtimeState>(INITIAL_STATE);

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
        socket = new WebSocket(simulationWsUrl());
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
        let envelope: SimulationEnvelope;
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
                  scenarioResults: prev.totals.scenarioResults + (envelope.scenario_results?.length ?? 0),
                  counterfactualResults:
                    prev.totals.counterfactualResults + (envelope.counterfactual_results?.length ?? 0)
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
