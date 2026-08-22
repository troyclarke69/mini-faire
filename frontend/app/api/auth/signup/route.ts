// Proxies to auth/auth_api.py's POST /auth/signup - creates a brand-new
// tenant and its first tenant_admin user, then applies the same session
// cookies login/route.ts does (signup logs the user straight in, matching
// auth_api.py's TokenResponse-on-signup design). See app/signup/page.tsx
// (this repo's onboarding wizard) for the form that calls this.

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
    backendResponse = await fetch(`${API_URL}/auth/signup`, {
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
    return NextResponse.json({ error: payload.detail ?? "signup failed" }, { status: backendResponse.status });
  }

  const response = NextResponse.json({
    user_id: payload.user_id,
    email: payload.email,
    role: payload.role,
    tenant_id: payload.tenant_id
  });
  return applyAuthCookies(response, payload);
}
