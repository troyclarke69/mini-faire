"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Building2, ChevronDown, LogOut } from "lucide-react";
import type { TenantSummary } from "@/lib/types";

// Header-level auth/tenant chrome (PHASE7-DEPLOYMENT.md Section 4) - lives
// in app/layout.tsx next to LiveModeToggle/ThemeToggle, visible on every
// page including the pre-Phase-7 dashboards (/retailers, /products, ...).
// This is the concrete shape "wire tenant filtering into the existing
// dashboards" takes for those pages: a persistent, global "who am I / which
// tenant" indicator, not per-row filtering of marts that were never
// tenant-scoped to begin with (see app/tenants/page.tsx's header comment
// for that reconciliation in full).
//
// A Client Component (needs onClick handlers + router.refresh()) fed by
// app/layout.tsx, a Server Component, which resolves the session/tenant
// list server-side via lib/auth.ts's getSession() - so no client-side fetch
// is needed just to know who's logged in.
export function TenantSwitcher({
  session,
  currentTenantId,
  tenants
}: {
  session: { email: string; role: string; tenantId: string } | null;
  currentTenantId: string | null;
  tenants: TenantSummary[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [isPending, startTransition] = useTransition();

  if (!session) {
    return (
      <Link
        href="/login"
        className="inline-flex h-10 items-center rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
      >
        Log in
      </Link>
    );
  }

  const currentTenant = tenants.find((t) => t.tenant_id === currentTenantId);
  const canSwitch = session.role === "admin" && tenants.length > 1;

  async function selectTenant(tenantId: string) {
    setOpen(false);
    await fetch("/api/tenant-context", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tenant_id: tenantId })
    });
    startTransition(() => router.refresh());
  }

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    startTransition(() => {
      router.push("/");
      router.refresh();
    });
  }

  return (
    <div className="relative flex items-center gap-2">
      <div className="relative">
        <button
          type="button"
          onClick={() => canSwitch && setOpen((v) => !v)}
          disabled={!canSwitch}
          className="inline-flex h-10 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 disabled:cursor-default dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          title={canSwitch ? "Switch tenant" : undefined}
        >
          <Building2 className="h-4 w-4" aria-hidden="true" />
          <span>{currentTenant?.name ?? currentTenantId ?? session.tenantId}</span>
          {canSwitch ? <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" /> : null}
        </button>
        {open && canSwitch ? (
          <div className="absolute right-0 z-30 mt-1 w-56 rounded-md border border-slate-200 bg-white py-1 shadow-panel dark:border-slate-800 dark:bg-slate-950">
            {tenants.map((tenant) => (
              <button
                key={tenant.tenant_id}
                type="button"
                onClick={() => selectTenant(tenant.tenant_id)}
                className={`block w-full px-3 py-2 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-900 ${
                  tenant.tenant_id === currentTenantId ? "font-semibold text-mint" : "text-slate-700 dark:text-slate-200"
                }`}
              >
                {tenant.name}
                <span className="ml-1 text-xs text-slate-400">({tenant.tenant_id})</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>
      <span className="hidden text-xs text-slate-500 dark:text-slate-400 sm:inline">
        {session.email} · {session.role}
      </span>
      <button
        type="button"
        onClick={logout}
        disabled={isPending}
        className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-slate-300 bg-white text-slate-700 hover:bg-slate-100 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
        aria-label="Log out"
        title="Log out"
      >
        <LogOut className="h-4 w-4" aria-hidden="true" />
      </button>
    </div>
  );
}
