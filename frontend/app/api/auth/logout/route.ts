// Proxies to auth/auth_api.py's POST /auth/logout (revokes the refresh
// token server-side) and clears every session cookie regardless of whether
// the backend call succeeds - matching auth_api.py's own logout() posture
// ("logout should never fail just because the client's token was already
// bad").

import { NextResponse } from "next/server";
import { clearAuthCookies, getRefreshToken } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export async function POST() {
  const refreshToken = getRefreshToken();
  if (refreshToken) {
    try {
      await fetch(`${API_URL}/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store"
      });
    } catch {
      // Best-effort - the cookies get cleared below regardless, which is
      // what actually ends this browser's session.
    }
  }
  return clearAuthCookies(NextResponse.json({ status: "ok" }));
}
