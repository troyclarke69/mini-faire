"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Secondary in-page navigation across the six Phase 6 ML routes, the same
// pattern components/monitoring/MonitoringTabs.tsx establishes for the
// monitoring section: the app's main sidebar (app/layout.tsx) stays flat
// (one "ML" entry pointing at /ml) rather than growing a nested submenu,
// and this tab strip at the top of every /ml/* page covers in-section
// navigation instead.

const TABS = [
  { href: "/ml", label: "Overview" },
  { href: "/ml/forecasts", label: "Forecasts" },
  { href: "/ml/clusters", label: "Clusters" },
  { href: "/ml/recommendations", label: "Recommendations" },
  { href: "/ml/anomalies", label: "Anomaly Classifications" },
  { href: "/ml/models", label: "Models" }
];

export function MLTabs() {
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
                ? "border-b-2 border-plum text-plum"
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
