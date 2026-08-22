"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useRealtimeUpdates, type RealtimeState } from "@/lib/realtime";

// PHASE4-REALTIME&STREAMING.md Section 6C: the "Live Mode: ON/OFF" toggle's
// state and the single shared WebSocket connection both live here, in one
// React context, so every page/table/graph reads from the same place
// instead of each opening its own socket.
//
// Server Components (every page.tsx / table component in this app) can't
// use hooks or WebSockets directly, so "live" here means: when an "update"
// envelope arrives, debounce a router.refresh() - which re-runs the current
// route's Server Components and re-fetches through lib/api.ts. A short
// fallback interval also fires router.refresh() periodically while Live
// Mode is on, so tables/charts keep moving even if the socket is
// reconnecting.

const STORAGE_KEY = "mini-faire-live-mode";
const PUSH_REFRESH_DEBOUNCE_MS = 3000;
const FALLBACK_REFRESH_INTERVAL_MS = 10000;

type LiveModeContextValue = RealtimeState & {
  liveMode: boolean;
  setLiveMode: (value: boolean) => void;
};

const LiveModeContext = createContext<LiveModeContextValue | null>(null);

export function LiveModeProvider({ children }: { children: React.ReactNode }) {
  const [liveMode, setLiveModeState] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const router = useRouter();
  const pushRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Read the stored preference after mount only (not during SSR) so the
  // server-rendered markup and the first client render match; flipping
  // liveMode on a moment later avoids a hydration mismatch warning.
  useEffect(() => {
    try {
      setLiveModeState(window.localStorage.getItem(STORAGE_KEY) === "on");
    } catch {
      // localStorage unavailable (private browsing, etc.) - stay off.
    }
    setHydrated(true);
  }, []);

  const realtime = useRealtimeUpdates(hydrated && liveMode);

  function setLiveMode(value: boolean) {
    setLiveModeState(value);
    try {
      window.localStorage.setItem(STORAGE_KEY, value ? "on" : "off");
    } catch {
      // ignore - preference just won't persist across reloads
    }
  }

  // Push-triggered refresh: bounded to at most once every
  // PUSH_REFRESH_DEBOUNCE_MS from the first "update" in a burst, so a flurry
  // of streamed events collapses into one refresh instead of many.
  useEffect(() => {
    if (!liveMode || realtime.lastEvent?.type !== "update") return;
    if (pushRefreshTimerRef.current) return;
    pushRefreshTimerRef.current = setTimeout(() => {
      pushRefreshTimerRef.current = null;
      router.refresh();
    }, PUSH_REFRESH_DEBOUNCE_MS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [realtime.lastEvent, liveMode]);

  useEffect(() => {
    return () => {
      if (pushRefreshTimerRef.current) clearTimeout(pushRefreshTimerRef.current);
    };
  }, []);

  // Fallback cadence, independent of the socket's health.
  useEffect(() => {
    if (!liveMode) return;
    const interval = setInterval(() => router.refresh(), FALLBACK_REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveMode]);

  return <LiveModeContext.Provider value={{ ...realtime, liveMode, setLiveMode }}>{children}</LiveModeContext.Provider>;
}

export function useLiveMode(): LiveModeContextValue {
  const ctx = useContext(LiveModeContext);
  if (!ctx) {
    throw new Error("useLiveMode must be used within a LiveModeProvider");
  }
  return ctx;
}
