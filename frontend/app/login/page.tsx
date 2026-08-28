"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { PageHeader } from "@/components/PageHeader";

// Seeded by scripts/seed_demo_tenant.py (idempotent - safe to re-run) into
// a shared "demo_tenant" workspace with synthetic order data from
// scripts/seed_tenant_orders.py. Published here on purpose so this page can
// pre-fill and label it - never a pattern for a real deployment's actual
// accounts (see that script's module docstring). If someone runs this app
// without ever running seed_demo_tenant.py, logging in with these still
// just fails normally (401 from /auth/login) - this page doesn't assume
// the demo account exists, it only offers it.
const DEMO_EMAIL = "demo@rmap.local";
const DEMO_PASSWORD = "demo_tenant";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState(DEMO_EMAIL);
  const [password, setPassword] = useState(DEMO_PASSWORD);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function fillDemoCredentials() {
    setEmail(DEMO_EMAIL);
    setPassword(DEMO_PASSWORD);
    setError(null);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password })
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(payload.error ?? "login failed");
        return;
      }
      router.push("/tenants");
      router.refresh(); // re-run the Server Component layout so the header picks up the new session
    } catch {
      setError("could not reach the login endpoint");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-sm space-y-6">
      <PageHeader title="Log in" subtitle="RMAP's tenant/auth layer (PHASE7-DEPLOYMENT.md Section 3)." />
      <div className="panel space-y-2 border-mint/40 bg-mint/5 p-4 text-sm">
        <p className="font-medium text-slate-700 dark:text-slate-300">
          Trying this out? The fields below are pre-filled with a shared demo workspace - just click
          &ldquo;Log in&rdquo;, no need to create a workspace of your own.
        </p>
        <button
          type="button"
          onClick={fillDemoCredentials}
          className="text-sm font-medium text-mint underline-offset-2 hover:underline"
        >
          Reset to demo credentials
        </button>
      </div>
      <form onSubmit={handleSubmit} className="panel space-y-4 p-6">
        <div className="space-y-1">
          <label htmlFor="email" className="text-sm font-medium text-slate-700 dark:text-slate-300">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="password" className="text-sm font-medium text-slate-700 dark:text-slate-300">
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
          />
        </div>
        {error ? <p className="text-sm text-coral">{error}</p> : null}
        <button
          type="submit"
          disabled={submitting}
          className="h-9 w-full rounded-md bg-mint text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-60"
        >
          {submitting ? "Logging in…" : "Log in"}
        </button>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Want your own isolated workspace instead of the shared demo one?{" "}
          <Link href="/signup" className="font-medium text-mint">
            Create a workspace
          </Link>
        </p>
      </form>
    </div>
  );
}
