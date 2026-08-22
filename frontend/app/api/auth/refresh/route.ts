// Proxies to auth/auth_api.py's POST /auth/refresh - exchanges the
// (still-valid) refresh token for a new access/refresh pair, rotating both
// (see that route's docstring on refresh-token rotation). Called by
// components/TenantSwitcher.tsx when a Server Component render observes a
// null session but a refresh-token cookie is still present (see that
// component's docstring for the exact trigger).
//
// What's NOT built here: an automatic retry-on-401 interceptor in front of
// every lib/api.ts authApi call - Server Component fetches (where authApi
// is actually used) can't run client-side refresh logic mid-render, so a
// truly transparent silent-refresh would need either middleware.ts
// intercepting every request or moving tenant data-fetching to the client.
// Given this app's fetch-in-the-Server-Component convention (every existing
// page.tsx already works this way) and a 15-minute access token TTL, an
// explicit re-login/refresh trigger on an expired session was the smaller,
// more consistent change - documented here rather than silently assumed
// solved.

import { NextResponse } from "next/server";
import { applyAuthCookies, clearAuthCookies, getRefreshToken } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export async function POST() {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    return NextResponse.json({ error: "no refresh token" }, { status: 401 });
  }

  let backendResponse: Response;
  try {
    backendResponse = await fetch(`${API_URL}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store"
    });
  } catch {
    return NextResponse.json({ error: "could not reach the backend API" }, { status: 502 });
  }

  const payload = await backendResponse.json().catch(() => ({}));
  if (!backendResponse.ok) {
    // The refresh token itself is no longer valid - clear cookies so the
    // UI falls back to a clean "log in" state instead of retrying forever.
    return clearAuthCookies(
      NextResponse.json({ error: payload.detail ?? "session expired" }, { status: backendResponse.status })
    );
  }

  const response = NextResponse.json({
    user_id: payload.user_id,
    email: payload.email,
    role: payload.role,
    tenant_id: payload.tenant_id
  });
  return applyAuthCookies(response, payload);
}
