"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Secondary in-page navigation across the five Phase 8 simulation routes,
// the same pattern components/ml/MLTabs.tsx and components/monitoring/
// MonitoringTabs.tsx establish: the app's main sidebar (app/layout.tsx)
// stays flat (one "Simulation" entry pointing at /simulation) and this tab
// strip covers in-section navigation instead.

const TABS = [
  { href: "/simulation", label: "Overview" },
  { href: "/simulation/scenarios", label: "Scenarios" },
  { href: "/simulation/counterfactuals", label: "Counterfactuals" },
  { href: "/simulation/agents", label: "Agents" },
  { href: "/simulation/results", label: "Results" }
];

export function SimulationTabs() {
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
