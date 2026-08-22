"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2 } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";

// Tenant onboarding wizard (PHASE7-DEPLOYMENT.md Section 4): two steps -
// (1) collect the new workspace's name plus its first user (who becomes
// that tenant's tenant_admin - see auth/auth_api.py's signup() docstring),
// (2) confirm the new tenant_id and hand off to /tenants. Kept to two
// client-side steps in one page rather than a multi-route wizard - signup
// is a single backend call (POST /auth/signup), there's no intermediate
// server round-trip that would justify separate pages/routes per step.

type Step = "form" | "done";

export default function SignupPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("form");
  const [organizationName, setOrganizationName] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [tenantId, setTenantId] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch("/api/auth/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ organization_name: organizationName, name, email, password })
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(payload.error ?? "signup failed");
        return;
      }
      setTenantId(payload.tenant_id);
      setStep("done");
      router.refresh(); // picks up the new session for the header/nav immediately
    } catch {
      setError("could not reach the signup endpoint");
    } finally {
      setSubmitting(false);
    }
  }

  if (step === "done") {
    return (
      <div className="mx-auto max-w-sm space-y-6">
        <PageHeader title="Workspace created" subtitle="Your new tenant is ready." />
        <div className="panel space-y-4 p-6 text-center">
          <CheckCircle2 className="mx-auto h-10 w-10 text-mint" aria-hidden="true" />
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Tenant ID: <span className="font-mono font-medium text-slate-900 dark:text-white">{tenantId}</span>
          </p>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Share this ID (and an invite link, once your teammate has it) with anyone joining this workspace via{" "}
            <span className="font-mono">/auth/join</span>.
          </p>
          <Link
            href="/tenants"
            className="inline-flex h-9 w-full items-center justify-center rounded-md bg-mint text-sm font-medium text-white transition hover:opacity-90"
          >
            Go to your dashboard
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-sm space-y-6">
      <PageHeader title="Create a workspace" subtitle="Sets up a new tenant and makes you its first admin." />
      <form onSubmit={handleSubmit} className="panel space-y-4 p-6">
        <div className="space-y-1">
          <label htmlFor="organization_name" className="text-sm font-medium text-slate-700 dark:text-slate-300">
            Organization name
          </label>
          <input
            id="organization_name"
            required
            value={organizationName}
            onChange={(event) => setOrganizationName(event.target.value)}
            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="name" className="text-sm font-medium text-slate-700 dark:text-slate-300">
            Your name
          </label>
          <input
            id="name"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
          />
        </div>
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
            minLength={10}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
          />
          <p className="text-xs text-slate-500 dark:text-slate-400">At least 10 characters (config/auth.yaml).</p>
        </div>
        {error ? <p className="text-sm text-coral">{error}</p> : null}
        <button
          type="submit"
          disabled={submitting}
          className="h-9 w-full rounded-md bg-mint text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-60"
        >
          {submitting ? "Creating workspace…" : "Create workspace"}
        </button>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-mint">
            Log in
          </Link>
        </p>
      </form>
    </div>
  );
}
