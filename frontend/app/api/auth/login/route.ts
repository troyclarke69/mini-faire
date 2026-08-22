// Proxies to auth/auth_api.py's POST /auth/login and, on success, sets the
// httpOnly session cookies - see lib/auth.ts's module docstring for why
// this indirection exists (the browser never holds the JWTs directly).
// Called from a "use client" login form (app/login/page.tsx) via
// `fetch("/api/auth/login", ...)`, same-origin, so no CORS concerns unlike
// api/metrics_api.py's browser-direct WebSocket/SSE calls.

import { NextResponse } from "next/server";
import { applyAuthCookies } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid request body" }, { status: 400 });
  }

  let backendResponse: Response;
  try {
    backendResponse = await fetch(`${API_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store"
    });
  } catch {
    return NextResponse.json({ error: "could not reach the backend API" }, { status: 502 });
  }

  const payload = await backendResponse.json().catch(() => ({}));
  if (!backendResponse.ok) {
    return NextResponse.json({ error: payload.detail ?? "login failed" }, { status: backendResponse.status });
  }

  const response = NextResponse.json({
    user_id: payload.user_id,
    email: payload.email,
    role: payload.role,
    tenant_id: payload.tenant_id
  });
  return applyAuthCookies(response, payload);
}
