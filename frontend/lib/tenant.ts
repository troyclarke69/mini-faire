// Current-tenant-context resolution (PHASE7-DEPLOYMENT.md Section 4).
//
// For every role except `admin`, "which tenant am I looking at" is simply
// "my own tenant" - the access token's `tenant_id` claim, full stop (the
// backend's require_tenant() enforces exactly this: a non-admin's path
// tenant_id must match their own). `admin` is the one role that can cross
// tenant boundaries (auth/auth_middleware.py's require_tenant() docstring:
// "the one role that's allowed to cross tenant boundaries - e.g. a support
// operator looking at a specific tenant's data") - for that role alone,
// "current tenant" is a UI choice (components/TenantSwitcher.tsx), stored in
// the `mf_tenant_context` cookie, not something the JWT determines.
//
// Server-only (next/headers) - same posture as lib/auth.ts.

import { cookies } from "next/headers";
import { getSession, TENANT_CONTEXT_COOKIE, type Session } from "./auth";

export function getCurrentTenantId(session: Session | null = getSession()): string | null {
  if (!session) return null;
  if (session.claims.role === "admin") {
    const chosen = cookies().get(TENANT_CONTEXT_COOKIE)?.value;
    if (chosen) return chosen;
  }
  return session.claims.tenant_id;
}
