"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Secondary in-page navigation across the six Phase 9 autonomy routes, same
// pattern components/simulation/SimulationTabs.tsx/components/ml/MLTabs.tsx
// establish: the app's main sidebar (app/layout.tsx) stays flat (one
// "Autonomy" entry pointing at /autonomy) and this tab strip covers
// in-section navigation instead. marigold is this section's accent color
// (tailwind.config.ts) - plum is Simulation's, mint is Monitoring's.

const TABS = [
  { href: "/autonomy", label: "Overview" },
  { href: "/autonomy/decisions", label: "Decisions" },
  { href: "/autonomy/conflicts", label: "Conflicts" },
  { href: "/autonomy/performance", label: "Performance" },
  { href: "/autonomy/agents", label: "Agents" },
  { href: "/autonomy/run", label: "Run" }
];

export function AutonomyTabs() {
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
                ? "border-b-2 border-marigold text-marigold"
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
