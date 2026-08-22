// Sets/clears the `mf_tenant_context` cookie lib/tenant.ts's
// getCurrentTenantId() reads - the admin-only "which tenant am I viewing"
// override (see that module's docstring). Deliberately does NOT check the
// caller's role here: the backend's require_tenant() dependency is the
// real enforcement point (a non-admin who somehow sets this cookie to
// another tenant's ID still gets a 403 from every /tenants/{tenant_id}/*
// call, per api/tenant_api.py) - this route just persists a UI preference,
// the same trust boundary lib/auth.ts's getSession() docstring already
// explains for decoded-but-unverified JWT claims.

import { NextResponse } from "next/server";
import { TENANT_CONTEXT_COOKIE } from "@/lib/auth";

export async function POST(request: Request) {
  const { tenant_id: tenantId } = (await request.json().catch(() => ({}))) as { tenant_id?: string };
  const response = NextResponse.json({ status: "ok" });
  if (tenantId) {
    response.cookies.set(TENANT_CONTEXT_COOKIE, tenantId, {
      httpOnly: false, // read client-side too, so TenantSwitcher can show the active choice without a round-trip
      sameSite: "lax",
      path: "/"
    });
  } else {
    response.cookies.delete(TENANT_CONTEXT_COOKIE);
  }
  return response;
}
