"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { simulationApiBase } from "@/lib/simulationRealtime";
import { money, number } from "@/lib/api";
import type { CounterfactualCatalog } from "@/lib/types";

// Client form for PHASE8-SIMULATION.md Section 7's CounterfactualBuilder -
// POSTs one ad-hoc counterfactual to api/simulation_api.py's
// POST /simulation/counterfactuals. Same "generic form driven by the
// backend's own param schema" choice components/simulation/
// ScenarioBuilder.tsx makes and documents - see that file's header comment.

const NUMERIC_PARAM_KEYS = new Set(["price_multiplier", "window_hours"]);

type CounterfactualRunResponse = {
  counterfactual_id: string;
  counterfactual_type: string;
  actual_order_count: number;
  counterfactual_order_count: number;
  actual_gmv: number;
  counterfactual_gmv: number;
  counterfactual_gmv_delta: number;
  retailer_diffs: { retailer_id: string; net_revenue_actual: number; net_revenue_counterfactual: number; net_revenue_delta: number }[];
  product_diffs: { product_id: string; units_sold_actual: number; units_sold_counterfactual: number; net_revenue_delta: number }[];
  removed_or_modified_order_ids: string[];
  status: string;
};

function coerceParamValue(key: string, raw: string): unknown {
  if (raw === "") return undefined;
  if (NUMERIC_PARAM_KEYS.has(key)) {
    const parsed = Number(raw);
    return Number.isNaN(parsed) ? raw : parsed;
  }
  return raw;
}

export function CounterfactualBuilder({ catalog }: { catalog: CounterfactualCatalog }) {
  const router = useRouter();
  const counterfactualTypes = catalog.counterfactual_types ?? [];
  const [counterfactualType, setCounterfactualType] = useState(counterfactualTypes[0] ?? "");
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [replayTicks, setReplayTicks] = useState(14);
  const [seed, setSeed] = useState(42);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CounterfactualRunResponse | null>(null);

  const paramSchema = catalog.param_schema?.[counterfactualType] ?? {};

  function handleTypeChange(next: string) {
    setCounterfactualType(next);
    setParamValues({});
    setResult(null);
    setError(null);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setResult(null);

    const params: Record<string, unknown> = {};
    for (const key of Object.keys(paramSchema)) {
      const coerced = coerceParamValue(key, paramValues[key] ?? "");
      if (coerced !== undefined) params[key] = coerced;
    }

    try {
      const response = await fetch(`${simulationApiBase()}/simulation/counterfactuals`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          counterfactual_type: counterfactualType,
          params,
          start_date: startDate || null,
          end_date: endDate || null,
          replay_ticks: replayTicks,
          seed
        })
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(typeof payload.detail === "string" ? payload.detail : "counterfactual run failed");
        return;
      }
      setResult(payload as CounterfactualRunResponse);
      router.refresh();
    } catch {
      setError("could not reach the simulation API");
    } finally {
      setSubmitting(false);
    }
  }

  if (counterfactualTypes.length === 0) {
    return (
      <div className="panel p-6 text-sm text-slate-500 dark:text-slate-400">
        No counterfactual types available - is api/simulation_api.py mounted and reachable?
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="panel space-y-4 p-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="space-y-1">
          <label htmlFor="counterfactual-type" className="text-sm font-medium text-slate-700 dark:text-slate-300">
            Counterfactual type
          </label>
          <select
            id="counterfactual-type"
            value={counterfactualType}
            onChange={(event) => handleTypeChange(event.target.value)}
            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
          >
            {counterfactualTypes.map((type) => (
              <option key={type} value={type}>
                {type.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <label htmlFor="start-date" className="text-sm font-medium text-slate-700 dark:text-slate-300">
            Start date (optional)
          </label>
          <input
            id="start-date"
            type="date"
            value={startDate}
            onChange={(event) => setStartDate(event.target.value)}
            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="end-date" className="text-sm font-medium text-slate-700 dark:text-slate-300">
            End date (optional)
          </label>
          <input
            id="end-date"
            type="date"
            value={endDate}
            onChange={(event) => setEndDate(event.target.value)}
            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="replay-ticks" className="text-sm font-medium text-slate-700 dark:text-slate-300">
            Replay ticks
          </label>
          <input
            id="replay-ticks"
            type="number"
            min={0}
            value={replayTicks}
            onChange={(event) => setReplayTicks(Number(event.target.value))}
            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
          />
        </div>
      </div>

      {Object.keys(paramSchema).length > 0 ? (
        <div className="space-y-1">
          <p className="text-sm font-medium text-slate-700 dark:text-slate-300">Counterfactual params</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {Object.entries(paramSchema).map(([key, hint]) => (
              <div key={key} className="space-y-1">
                <label htmlFor={`cf-param-${key}`} className="text-xs text-slate-500">
                  {key} <span className="text-slate-400">({hint})</span>
                </label>
                <input
                  id={`cf-param-${key}`}
                  type="text"
                  value={paramValues[key] ?? ""}
                  onChange={(event) => setParamValues((prev) => ({ ...prev, [key]: event.target.value }))}
                  className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
                />
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {error ? <p className="text-sm text-coral">{error}</p> : null}

      <button
        type="submit"
        disabled={submitting}
        className="h-9 rounded-md bg-plum px-4 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-60"
      >
        {submitting ? "Running…" : "Run counterfactual"}
      </button>

      {result ? (
        <div className="space-y-3 rounded-md border border-plum/30 bg-plum/5 p-4 dark:border-plum/30 dark:bg-plum/10">
          <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
            {result.counterfactual_type.replace(/_/g, " ")} - actual history vs. counterfactual history
          </p>
          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <p className="text-xs text-slate-500">Actual GMV</p>
              <p className="text-lg font-semibold text-slate-950 dark:text-white">{money(result.actual_gmv)}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Counterfactual GMV</p>
              <p className="text-lg font-semibold text-slate-950 dark:text-white">{money(result.counterfactual_gmv)}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Delta</p>
              <p className={`text-lg font-semibold ${result.counterfactual_gmv_delta >= 0 ? "text-mint" : "text-coral"}`}>
                {result.counterfactual_gmv_delta >= 0 ? "+" : ""}
                {money(result.counterfactual_gmv_delta)}
              </p>
            </div>
          </div>
          <p className="text-xs text-slate-500">
            {result.actual_order_count} actual order(s) vs. {result.counterfactual_order_count} counterfactual order(s) -{" "}
            {result.removed_or_modified_order_ids.length} order(s) removed or modified.
          </p>
          {result.retailer_diffs.length > 0 ? (
            <div>
              <p className="mb-1 text-xs font-medium text-slate-500">Biggest retailer impacts</p>
              <ul className="space-y-1 text-xs text-slate-600 dark:text-slate-300">
                {result.retailer_diffs.slice(0, 5).map((r) => (
                  <li key={r.retailer_id}>
                    {r.retailer_id}: {money(r.net_revenue_actual)} -&gt; {money(r.net_revenue_counterfactual)} (
                    {r.net_revenue_delta >= 0 ? "+" : ""}
                    {number(r.net_revenue_delta, 2)})
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </form>
  );
}
