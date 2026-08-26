import type { Metadata } from "next";
import { Activity, AlertTriangle, Bot, Boxes, Brain, Building2, FlaskConical, GitBranch, Home, Package, ReceiptText, ServerCog, ShieldAlert } from "lucide-react";
import "./globals.css";
import { LiveModeProvider } from "@/components/LiveModeProvider";
import { LiveModeToggle } from "@/components/LiveModeToggle";
import { ThemeToggle } from "@/components/ThemeToggle";
import { NavLink } from "@/components/NavLink";
import { TenantSwitcher } from "@/components/TenantSwitcher";
import { authApi } from "@/lib/api";
import { getSession } from "@/lib/auth";
import { getCurrentTenantId } from "@/lib/tenant";
import type { TenantSummary } from "@/lib/types";

export const metadata: Metadata = {
  title: "Mini Faire",
  description: "Retail marketplace analytics platform"
};

const navItems = [
  { href: "/", label: "Dashboard", icon: Home },
  { href: "/retailers", label: "Retailers", icon: Activity },
  { href: "/products", label: "Products", icon: Package },
  { href: "/orders", label: "Orders", icon: ReceiptText },
  { href: "/compute", label: "Compute", icon: Boxes },
  { href: "/lineage", label: "Lineage", icon: GitBranch },
  { href: "/quarantine", label: "Quarantine", icon: ShieldAlert },
  { href: "/model-runs", label: "Model Runs", icon: ServerCog },
  { href: "/monitoring", label: "Monitoring", icon: AlertTriangle },
  { href: "/ml", label: "ML", icon: Brain },
  // Phase 8 (PHASE8-SIMULATION.md Section 7) - digital twin, ABM
  // scenarios, and counterfactual replay. Open like /ml and /monitoring
  // (no tenant gating - see api/simulation_api.py's module docstring).
  { href: "/simulation", label: "Simulation", icon: FlaskConical },
  // Phase 9 (PHASE9-AUTONOMY.md Section 9) - the autonomous agent decision
  // layer built on top of every earlier phase's warehouse/ML/anomaly/
  // simulation infrastructure. Open like /simulation/ml/monitoring above (no
  // tenant gating - see api/autonomy_api.py's module docstring). marigold is
  // this section's own accent color (tailwind.config.ts) - distinct from
  // plum (Simulation) and mint (Monitoring) - carried through
  // components/autonomy/AutonomyTabs.tsx and AutonomyLiveBar.tsx.
  { href: "/autonomy", label: "Autonomy", icon: Bot },
  // Phase 7 (PHASE7-DEPLOYMENT.md Section 2/4) - the one nav item backed by
  // genuinely tenant-scoped data (marts.compute_tenant_health /
  // marts.metrics_tenant_daily), gated behind login inside the page itself
  // rather than hidden from logged-out users here - matching this app's
  // existing "every route is reachable, some render an empty/prompt state"
  // convention (e.g. /quarantine before any run has ever quarantined a
  // record).
  { href: "/tenants", label: "Tenants", icon: Building2 }
];

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const session = getSession();
  // Every role gets at least its own tenant back from GET /tenants (see
  // api/tenant_api.py's tenants_index()) - fetched here, once, so both the
  // header switcher and app/tenants/page.tsx's default selection have it
  // without a second round-trip.
  const tenants: TenantSummary[] = session ? await authApi.tenants(session.accessToken) : [];
  const currentTenantId = session ? getCurrentTenantId(session) : null;

  return (
    <html lang="en" suppressHydrationWarning>
      <body className="bg-paper text-ink antialiased dark:bg-slate-950 dark:text-slate-100">
        <LiveModeProvider>
          <div className="flex min-h-screen">
            <aside className="hidden w-64 shrink-0 border-r border-slate-200 bg-white px-4 py-5 dark:border-slate-800 dark:bg-slate-950 lg:block">
              <div className="mb-8">
                <p className="text-lg font-semibold">Mini Faire</p>
                <p className="text-sm text-slate-500">Marketplace analytics</p>
              </div>
              <nav className="space-y-1">
                {navItems.map((item) => (
                  <NavLink
                    key={item.href}
                    href={item.href}
                    label={item.label}
                    icon={<item.icon className="h-4 w-4" aria-hidden="true" />}
                  />
                ))}
              </nav>
            </aside>
            <div className="min-w-0 flex-1">
              <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-slate-200 bg-paper/90 px-4 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90 sm:px-6">
                <div>
                  <p className="text-sm font-medium text-slate-500">Environment</p>
                  <p className="text-base font-semibold">Local DuckDB Demo</p>
                </div>
                <div className="flex items-center gap-2">
                  <TenantSwitcher
                    session={session ? { email: session.claims.email, role: session.claims.role, tenantId: session.claims.tenant_id } : null}
                    currentTenantId={currentTenantId}
                    tenants={tenants}
                  />
                  <LiveModeToggle />
                  <ThemeToggle />
                </div>
              </header>
              <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">{children}</main>
            </div>
          </div>
        </LiveModeProvider>
      </body>
    </html>
  );
}

