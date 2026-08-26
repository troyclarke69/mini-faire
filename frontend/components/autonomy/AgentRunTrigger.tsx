"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AgentConflictViewer } from "@/components/autonomy/AgentConflictViewer";
import { money, number } from "@/lib/api";
import { autonomyApiBase } from "@/lib/autonomyRealtime";
import type { AgentRunSummary } from "@/lib/types";

// Client form for PHASE9-AUTONOMY.md Section 9's "Run" trigger - POSTs one
// ad-hoc orchestration/agent_flow.run_agent_flow() pass straight to
// api/autonomy_api.py's POST /autonomy/run (no auth token needed - see that
// module's docstring), the interactive counterpart to
// `python orchestration/agent_flow.py` / a scheduled external caller.
//
// Mode choices are hardcoded to orchestration/agent_flow.py's RUN_MODES =
// ("live", "tick", "scenario") rather than fetched from a catalog endpoint -
// unlike Phase 8's nine scenario types (components/simulation/
// ScenarioBuilder.tsx, driven by GET /simulation/scenarios's param schema),
// this is a fixed three-value enum unlikely to grow, so a small hardcoded
// list here is simpler than adding a dedicated GET endpoint just to expose
// three constant strings.

const RUN_MODES = ["live", "tick", "scenario"] as const;

export function AgentRunTrigger() {
  const router = useRouter();
  const [mode, setMode] = useState<(typeof RUN_MODES)[number]>("live");
  const [scenarioType, setScenarioType] = useState("demand_shock");
  const [rounds, setRounds] = useState(2);
  const [ticksPerRound, setTicksPerRound] = useState(1);
  const [seed, setSeed] = useState(42);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AgentRunSummary | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setResult(null);

    const body: Record<string, unknown> = { mode, rounds, ticks_per_round: ticksPerRound, seed };
    if (mode === "scenario") body.scenario_type = scenarioType;

    try {
      const response = await fetch(`${autonomyApiBase()}/autonomy/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(typeof payload.detail === "string" ? payload.detail : "agent run failed");
        return;
      }
      if (!payload || !payload.run_id) {
        setError("Warehouse not built yet - run scripts/run_demo.py first.");
        return;
      }
      setResult(payload as AgentRunSummary);
      router.refresh(); // picks up the new rows in the decisions/conflicts/performance tabs, once revalidated
    } catch {
      setError("could not reach the autonomy API");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={handleSubmit} className="panel space-y-4 p-6">
        <div className="grid gap-4 sm:grid-cols-4">
          <div className="space-y-1">
            <label htmlFor="run-mode" className="text-sm font-medium text-slate-700 dark:text-slate-300">
              Mode
            </label>
            <select
              id="run-mode"
              value={mode}
              onChange={(event) => setMode(event.target.value as (typeof RUN_MODES)[number])}
              className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
            >
              {RUN_MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          {mode === "scenario" ? (
            <div className="space-y-1">
              <label htmlFor="scenario-type" className="text-sm font-medium text-slate-700 dark:text-slate-300">
                Scenario type
              </label>
              <input
                id="scenario-type"
                type="text"
                value={scenarioType}
                onChange={(event) => setScenarioType(event.target.value)}
                className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
              />
            </div>
          ) : null}
          {mode === "tick" ? (
            <>
              <div className="space-y-1">
                <label htmlFor="rounds" className="text-sm font-medium text-slate-700 dark:text-slate-300">
                  Rounds
                </label>
                <input
                  id="rounds"
                  type="number"
                  min={1}
                  value={rounds}
                  onChange={(event) => setRounds(Number(event.target.value))}
                  className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
                />
              </div>
              <div className="space-y-1">
                <label htmlFor="ticks-per-round" className="text-sm font-medium text-slate-700 dark:text-slate-300">
                  Ticks per round
                </label>
                <input
                  id="ticks-per-round"
                  type="number"
                  min={1}
                  value={ticksPerRound}
                  onChange={(event) => setTicksPerRound(Number(event.target.value))}
                  className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
                />
              </div>
            </>
          ) : null}
          <div className="space-y-1">
            <label htmlFor="seed" className="text-sm font-medium text-slate-700 dark:text-slate-300">
              Random seed
            </label>
            <input
              id="seed"
              type="number"
              value={seed}
              onChange={(event) => setSeed(Number(event.target.value))}
              className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
            />
          </div>
        </div>

        {error ? <p className="text-sm text-coral">{error}</p> : null}

        <button
          type="submit"
          disabled={submitting}
          className="h-9 rounded-md bg-marigold px-4 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-60"
        >
          {submitting ? "Running…" : "Run agents"}
        </button>
      </form>

      {result ? (
        <div className="space-y-4">
          <div className="panel space-y-3 p-6">
            <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
              {result.mode} run {result.run_id} - {result.rounds} round{result.rounds === 1 ? "" : "s"}, pipeline{" "}
              {result.pipeline_healthy ? "healthy" : "degraded"}
            </p>
            <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <div>
                <p className="text-xs text-slate-500">Proposed</p>
                <p className="text-lg font-semibold text-slate-950 dark:text-white">{number(result.proposed_count)}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Applied</p>
                <p className="text-lg font-semibold text-mint">{number(result.applied_count)}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Advisory</p>
                <p className="text-lg font-semibold text-marigold">{number(result.advisory_count)}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Rejected</p>
                <p className="text-lg font-semibold text-coral">{number(result.rejected_count)}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Conflicts</p>
                <p className="text-lg font-semibold text-slate-950 dark:text-white">{number(result.conflicts?.length ?? 0)}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Reward (GMV delta)</p>
                <p className={`text-lg font-semibold ${result.reward >= 0 ? "text-mint" : "text-coral"}`}>
                  {result.reward >= 0 ? "+" : ""}
                  {money(result.reward)}
                </p>
              </div>
            </div>
            <p className="text-xs text-slate-500">
              Baseline projection GMV {money(result.gmv_before)} -&gt; {money(result.gmv_after)}, computed in{" "}
              {result.elapsed_seconds}s.
            </p>
          </div>
          {result.conflicts && result.conflicts.length > 0 ? (
            <AgentConflictViewer conflicts={result.conflicts} title="Conflicts Resolved This Run" />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
