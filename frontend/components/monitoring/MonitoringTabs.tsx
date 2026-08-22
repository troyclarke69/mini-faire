"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Secondary in-page navigation across the five Phase 5 monitoring routes.
// The app's main sidebar (app/layout.tsx) stays flat (one "Monitoring" entry
// pointing at /monitoring, matching every other top-level section like
// /compute or /lineage) rather than growing a nested submenu just for this
// one section - this small tab strip at the top of each monitoring page
// covers the same navigation need without changing the sidebar's structure.

const TABS = [
  { href: "/monitoring", label: "Overview" },
  { href: "/monitoring/anomalies", label: "Anomalies" },
  { href: "/monitoring/system", label: "System Metrics" },
  { href: "/monitoring/schema-drift", label: "Schema Drift" },
  { href: "/monitoring/alerts", label: "Alerts" }
];

export function MonitoringTabs() {
  const pathname = usePathname();

  return (
    <nav className="flex flex-wrap gap-1 border-b border-slate-200 dark:border-slate-800">
      {TABS.map((tab) => {
        const active = pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={`rounded-t-md px-3 py-2 text-sm font-medium transition ${
              active
                ? "border-b-2 border-mint text-mint"
                : "border-b-2 border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
