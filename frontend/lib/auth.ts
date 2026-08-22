// Auth session helpers (PHASE7-DEPLOYMENT.md Section 3/4).
//
// Server-only module (imports `next/headers`) - called from Server
// Components (app/layout.tsx, app/tenants/page.tsx) and Route Handlers
// (app/api/auth/*/route.ts), never from a "use client" component directly.
// The actual JWTs never reach client-side JavaScript: `access_token` and
// `refresh_token` are set as httpOnly cookies by the Route Handlers below,
// which is why every authenticated Server Component fetch in this file (and
// in lib/api.ts's tenant-aware fetchers) has to explicitly attach the
// Authorization header itself - the browser has no access to the cookie
// value to do that on the client's behalf, which is the whole point of
// httpOnly.
//
// `getSession()` decodes the access token's payload WITHOUT verifying its
// signature - this file has no access to JWT_SECRET_KEY (and shouldn't;
// that secret stays backend-only per config/auth.yaml's own comment), so
// this is display/routing convenience only ("whose name goes in the header",
// "which tenant is selected"), never an authorization decision. Every actual
// authorization check already happens on the backend
// (auth/auth_middleware.py's get_current_user/require_role/require_tenant,
// re-verified against JWT_SECRET_KEY on every request) - this file trusts
// the cookie's claims the same limited way a UI trusts anything it renders
// speculatively pending the real API call's response.

import { cookies } from "next/headers";
import type { NextResponse } from "next/server";

export const ACCESS_TOKEN_COOKIE = "mf_access_token";
export const REFRESH_TOKEN_COOKIE = "mf_refresh_token";
// Which tenant an admin has chosen to *view* - distinct from the tenant
// baked into their own access token (see lib/tenant.ts's module docstring
// for why admins are the one role that needs this).
export const TENANT_CONTEXT_COOKIE = "mf_tenant_context";

// Mirrors config/auth.yaml's jwt.access_token_ttl_seconds /
// refresh_token_ttl_seconds - the cookie's own maxAge is set to match so a
// browser-cleared-cookie and a backend-expired-token happen at roughly the
// same time rather than one outliving the other. Kept as a literal here
// rather than fetched from the backend at request time - config/auth.yaml
// isn't network-reachable from the frontend, and duplicating two integers
// is simpler than adding an endpoint just to read them.
export const ACCESS_TOKEN_TTL_SECONDS = 900;
export const REFRESH_TOKEN_TTL_SECONDS = 1_209_600;

export type SessionClaims = {
  sub: string;
  email: string;
  role: "admin" | "tenant_admin" | "analyst" | "viewer";
  tenant_id: string;
  exp?: number;
};

// admin-down, matching auth/auth_models.py's ROLES tuple/role_at_least().
const ROLE_RANK: Record<string, number> = { viewer: 0, analyst: 1, tenant_admin: 2, admin: 3 };

export function roleAtLeast(role: string | undefined, minimum: string): boolean {
  return (ROLE_RANK[role ?? ""] ?? -1) >= (ROLE_RANK[minimum] ?? Number.POSITIVE_INFINITY);
}

function decodeJwtPayload(token: string): SessionClaims | null {
  try {
    const [, payloadSegment] = token.split(".");
    if (!payloadSegment) return null;
    // base64url -> base64
    const base64 = payloadSegment.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");
    const json = Buffer.from(padded, "base64").toString("utf-8");
    const claims = JSON.parse(json) as SessionClaims;
    if (!claims.sub || !claims.tenant_id) return null;
    return claims;
  } catch {
    return null;
  }
}

export type Session = {
  accessToken: string;
  claims: SessionClaims;
};

// Server Components/Route Handlers only - `cookies()` throws outside a
// request context. Returns null both when there's no cookie at all (never
// logged in) and when the token is malformed/expired-looking (claims.exp in
// the past) - callers don't need to distinguish "logged out" from "session
// expired," both render the same "log in" prompt.
export function getSession(): Session | null {
  const accessToken = cookies().get(ACCESS_TOKEN_COOKIE)?.value;
  if (!accessToken) return null;
  const claims = decodeJwtPayload(accessToken);
  if (!claims) return null;
  if (claims.exp && claims.exp * 1000 < Date.now()) return null;
  return { accessToken, claims };
}

export function getRefreshToken(): string | null {
  return cookies().get(REFRESH_TOKEN_COOKIE)?.value ?? null;
}

// Shared by every Route Handler that gets a fresh token pair back from
// auth/auth_api.py (login/signup/join/refresh all return the same
// TokenResponse shape) - one place sets the cookie flags so they can't
// drift between routes. `secure` is conditional on NODE_ENV rather than
// always-on so this still works over plain http in local dev
// (docker-compose.cloud.yaml's default) - infra/cloud's actual deployment
// targets (fly/render/azure) all terminate TLS in front of this app, so
// `secure: true` is the real behavior whenever NODE_ENV=production.
export function applyAuthCookies(
  response: NextResponse,
  tokens: { access_token: string; refresh_token: string }
): NextResponse {
  const secure = process.env.NODE_ENV === "production";
  response.cookies.set(ACCESS_TOKEN_COOKIE, tokens.access_token, {
    httpOnly: true,
    secure,
    sameSite: "lax",
    path: "/",
    maxAge: ACCESS_TOKEN_TTL_SECONDS
  });
  response.cookies.set(REFRESH_TOKEN_COOKIE, tokens.refresh_token, {
    httpOnly: true,
    secure,
    sameSite: "lax",
    path: "/",
    maxAge: REFRESH_TOKEN_TTL_SECONDS
  });
  return response;
}

export function clearAuthCookies(response: NextResponse): NextResponse {
  response.cookies.delete(ACCESS_TOKEN_COOKIE);
  response.cookies.delete(REFRESH_TOKEN_COOKIE);
  response.cookies.delete(TENANT_CONTEXT_COOKIE);
  return response;
}
