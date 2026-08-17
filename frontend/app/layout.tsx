import type { Metadata } from "next";
import { Activity, Boxes, GitBranch, Home, Package, ReceiptText, ServerCog, ShieldAlert } from "lucide-react";
import "./globals.css";
import { ThemeToggle } from "@/components/ThemeToggle";
import { NavLink } from "@/components/NavLink";

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
  { href: "/model-runs", label: "Model Runs", icon: ServerCog }
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="bg-paper text-ink antialiased dark:bg-slate-950 dark:text-slate-100">
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
              <ThemeToggle />
            </header>
            <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}

