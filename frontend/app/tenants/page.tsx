import Link from "next/link";
import { PageHeader } from "@/components/PageHeader";
import { KpiCard } from "@/components/KpiCard";
import { EmptyState } from "@/components/EmptyState";
import { authApi, money, number, percent } from "@/lib/api";
import { getSession } from "@/lib/auth";
import { getCurrentTenantId } from "@/lib/tenant";

// Tenant settings + overview (PHASE7-DEPLOYMENT.md Section 4) - the one
// dashboard in this app backed by genuinely tenant-scoped data
// (marts.compute_tenant_health / marts.compute_tenant_growth /
// marts.metrics_tenant_daily, via api/tenant_api.py).
//
// Scope reconciliation, stated plainly rather than left implicit: the task
// this page and components/TenantSwitcher.tsx satisfy was originally framed
// as "wire tenant filtering into the existing dashboards" (/retailers,
// /products, /orders, /compute, /monitoring, /ml). Those dashboards' marts
// (marts.metrics_retailer_daily, marts.compute_retailer_health, etc.) have
// no tenant_id column - they're the pre-Phase-7 single-tenant demo
// warehouse, and ingestion/tenant_ingest.py's module docstring already
// documents that "orders" is the only entity carried end-to-end through the
// tenant-aware pipeline (compute/polars/tenant_metrics.py,
// ml/tenant_models/). Retrofitting a `tenant_id` filter onto those
// dashboards' queries would either silently return nothing (no such column)
// or require fabricating tenant assignment for rows that were never
// tenant-scoped to begin with - both worse than being explicit about it.
// What "wiring tenant filtering into the existing dashboards" concretely
// becomes here: a persistent, global tenant/auth indicator in the header on
// every page (components/TenantSwitcher.tsx, wired into app/layout.tsx),
// and this page as the one place real per-tenant metrics are shown - not
// per-row filtering of marts that don't carry a tenant_id.
export default async function TenantsPage() {
  const session = getSession();
  if (!session) {
    return (
      <div className="space-y-6">
        <PageHeader title="Tenants" subtitle="Log in to view a workspace's usage and settings." />
        <div className="panel space-y-3 p-6 text-center">
          <p className="text-sm text-slate-600 dark:text-slate-400">You need to be logged in to view this page.</p>
          {/* The two options below read as equally-weighted choices without this - "Log in" looks
              like it requires an account you don't have yet, so the natural instinct is to reach
              for "Create a workspace" instead, even though logging in (demo credentials pre-filled
              on /login, same as the isDemoTenant banner below once you're in) needs no setup at
              all. Spelling out which is which here, not just after landing on /login. */}
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Just trying this out? <span className="font-medium text-slate-700 dark:text-slate-300">Log in</span> -
            the shared demo workspace&rsquo;s credentials are pre-filled, nothing to create.{" "}
            <span className="font-medium text-slate-700 dark:text-slate-300">Create a workspace</span> is only for
            when you want your own isolated tenant instead.
          </p>
          <div className="mt-4 flex justify-center gap-3">
            <Link href="/login" className="inline-flex h-9 items-center rounded-md bg-mint px-4 text-sm font-medium text-white">
              Log in
            </Link>
            <Link
              href="/signup"
              className="inline-flex h-9 items-center rounded-md border border-slate-300 px-4 text-sm font-medium text-slate-700 dark:border-slate-700 dark:text-slate-200"
            >
              Create a workspace
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const tenantId = getCurrentTenantId(session);
  // "demo_tenant" - kept as a literal here rather than a shared import since
  // it's only ever compared, never used to authenticate anything (see
  // scripts/seed_demo_tenant.py's DEMO_TENANT_ID for the canonical
  // definition, and app/login/page.tsx's DEMO_EMAIL/DEMO_PASSWORD for the
  // login form's half of this same "no account needed to look around" story).
  const isDemoTenant = tenantId === "demo_tenant";
  const [tenant, health, growth, daily] = await Promise.all([
    tenantId ? authApi.tenant(tenantId, session.accessToken) : Promise.resolve(null),
    tenantId ? authApi.tenantHealth(tenantId, session.accessToken) : Promise.resolve(null),
    tenantId ? authApi.tenantGrowth(tenantId, session.accessToken) : Promise.resolve(null),
    tenantId ? authApi.tenantDaily(tenantId, session.accessToken) : Promise.resolve([])
  ]);

  const trendRows = [...daily].sort((a, b) => (a.order_date < b.order_date ? 1 : -1)).slice(0, 14);

  return (
    <div className="space-y-6">
      <PageHeader
        title={tenant?.name ?? tenantId ?? "Tenants"}
        subtitle={`Tenant ID: ${tenantId ?? "—"} · Isolation: ${tenant?.isolation_policy ?? "—"} · Status: ${tenant?.status ?? "—"}`}
      />

      {isDemoTenant ? (
        <div className="panel space-y-1 border-mint/40 bg-mint/5 p-4 text-sm">
          <p className="font-medium text-slate-700 dark:text-slate-300">
            You&rsquo;re viewing the shared demo workspace - the same one the login page pre-fills, no
            account of your own needed to look around.
          </p>
          <p className="text-slate-500 dark:text-slate-400">
            Want your own isolated workspace instead?{" "}
            <Link href="/signup" className="font-medium text-mint">
              Create a workspace
            </Link>
          </p>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard label="Lifetime GMV" value={money(health?.gmv)} detail={health ? `${number(health.order_count)} orders` : undefined} />
        <KpiCard label="Net revenue" value={money(health?.net_revenue)} />
        <KpiCard
          label="Tenant health score"
          value={health ? number(health.tenant_health_score, 1) : "—"}
          detail={health?.last_order_date ? `Last order ${health.last_order_date}` : undefined}
        />
        <KpiCard
          label="7-day GMV trend"
          value={growth?.growth_rate != null ? percent(growth.growth_rate) : "—"}
          detail={growth ? growth.trend.replace("_", " ") : "no data yet"}
        />
      </div>

      <div className="panel overflow-x-auto">
        <table className="w-full min-w-[640px]">
          <thead className="table-head">
            <tr>
              <th className="px-3 py-2">Date</th>
              <th className="px-3 py-2">Orders</th>
              <th className="px-3 py-2">Units</th>
              <th className="px-3 py-2">GMV</th>
              <th className="px-3 py-2">Net revenue</th>
              <th className="px-3 py-2">AOV</th>
            </tr>
          </thead>
          <tbody>
            {trendRows.map((row) => (
              <tr key={row.order_date}>
                <td className="table-cell">{row.order_date}</td>
                <td className="table-cell">{number(row.order_count)}</td>
                <td className="table-cell">{number(row.units_sold)}</td>
                <td className="table-cell">{money(row.gmv)}</td>
                <td className="table-cell">{money(row.net_revenue)}</td>
                <td className="table-cell">{money(row.average_order_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {trendRows.length === 0 ? (
          <EmptyState label="No tenant order data yet - run ingestion/tenant_ingest.py and warehouse/duckdb/tenant_elt.sql for this tenant." />
        ) : null}
      </div>

      <div className="panel p-4">
        <p className="text-sm font-medium text-slate-700 dark:text-slate-300">Signed in as</p>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          {session.claims.email} · role: {session.claims.role}
        </p>
      </div>
    </div>
  );
}
