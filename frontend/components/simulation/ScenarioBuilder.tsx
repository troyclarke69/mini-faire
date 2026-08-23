"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { simulationApiBase } from "@/lib/simulationRealtime";
import { money, number } from "@/lib/api";
import type { AgentStrategy, ScenarioCatalog } from "@/lib/types";

// Client form for PHASE8-SIMULATION.md Section 7's ScenarioBuilder -
// POSTs one ad-hoc scenario straight to api/simulation_api.py's
// POST /simulation/scenarios (no auth token needed - see that module's
// docstring), the interactive counterpart to the batch `python
// orchestration/simulation_flow.py` / POST /simulation/run triggers.
//
// The param fields below are built generically from `catalog.param_schema`
// (scenario_engine.SCENARIO_PARAM_SCHEMA) rather than one hardcoded form
// per of the nine scenario types - a param name is rendered as a number
// input if it's in NUMERIC_PARAM_KEYS, a comma-separated list if it's in
// LIST_PARAM_KEYS, else plain text. This is a deliberate "one generic form
// driven by the same schema the backend already validates against" choice
// over nine bespoke forms - it means a new scenario type the backend adds
// later needs no matching frontend change, at the cost of a slightly
// blunter per-field UI than a bespoke form would give each type.
//
// Strategy overrides are scoped to THIS run only (matches build_agents()'s
// "ephemeral, not persisted" reality - see lib/types.ts's AgentStrategy
// comment) - at most one retailer override and one product override, using
// a deliberately partial set of the most demo-relevant fields rather than
// every field on RetailerStrategy/ProductStrategy, to keep the form usable.
// components/simulation/AgentStrategyEditor.tsx is the read-only reference
// for every field's full default value.

const NUMERIC_PARAM_KEYS = new Set([
  "new_price",
  "delta",
  "multiplier",
  "delay_ticks",
  "duration_ticks",
  "pressure_increase",
  "pressure_decrease",
  "discount",
  "unit_price",
  "unit_cost",
  "inventory_count",
  "window_hours"
]);
const LIST_PARAM_KEYS = new Set(["retailer_ids"]);

type ScenarioRunResponse = {
  scenario_id: string;
  scenario_type: string;
  predicted_gmv_baseline: number;
  predicted_gmv_scenario: number;
  predicted_gmv_delta: number;
  predicted_velocity_baseline: number | null;
  predicted_velocity_scenario: number | null;
  predicted_retailer_health: { retailer_id: string; order_count: number; net_revenue: number; retailer_health_score: number }[];
  predicted_cluster_movement: { retailer_id: string; net_revenue_delta: number; movement_distance: number }[];
  predicted_recommendations: { product_id: string; product_name: string | null; demand_curve_point: number }[];
  status: string;
};

function coerceParamValue(key: string, raw: string): unknown {
  if (raw === "") return undefined;
  if (LIST_PARAM_KEYS.has(key)) {
    return raw.split(",").map((v) => v.trim()).filter(Boolean);
  }
  if (NUMERIC_PARAM_KEYS.has(key)) {
    const parsed = Number(raw);
    return Number.isNaN(parsed) ? raw : parsed;
  }
  return raw;
}

export function ScenarioBuilder({ catalog, agents }: { catalog: ScenarioCatalog; agents: AgentStrategy }) {
  const router = useRouter();
  const scenarioTypes = catalog.scenario_types ?? [];
  const [scenarioType, setScenarioType] = useState(scenarioTypes[0] ?? "");
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [ticks, setTicks] = useState(14);
  const [seed, setSeed] = useState(42);

  const [retailerOverrideId, setRetailerOverrideId] = useState("");
  const [promotionStrategy, setPromotionStrategy] = useState("");
  const [promotionDiscount, setPromotionDiscount] = useState("");
  const [mlDriven, setMlDriven] = useState(false);

  const [productOverrideId, setProductOverrideId] = useState("");
  const [priceElasticity, setPriceElasticity] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScenarioRunResponse | null>(null);

  const paramSchema = catalog.param_schema?.[scenarioType] ?? {};

  function handleScenarioTypeChange(next: string) {
    setScenarioType(next);
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

    const retailerOverrides: Record<string, Record<string, unknown>> = {};
    if (retailerOverrideId) {
      const fields: Record<string, unknown> = {};
      if (promotionStrategy) fields.promotion_strategy = promotionStrategy;
      if (promotionDiscount !== "") fields.promotion_discount = Number(promotionDiscount);
      if (mlDriven) fields.ml_driven = true;
      if (Object.keys(fields).length > 0) retailerOverrides[retailerOverrideId] = fields;
    }

    const productOverrides: Record<string, Record<string, unknown>> = {};
    if (productOverrideId && priceElasticity !== "") {
      productOverrides[productOverrideId] = { price_elasticity: Number(priceElasticity) };
    }

    try {
      const response = await fetch(`${simulationApiBase()}/simulation/scenarios`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenario_type: scenarioType,
          params,
          ticks,
          seed,
          retailer_strategy_overrides: Object.keys(retailerOverrides).length > 0 ? retailerOverrides : null,
          product_strategy_overrides: Object.keys(productOverrides).length > 0 ? productOverrides : null
        })
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(typeof payload.detail === "string" ? payload.detail : "scenario run failed");
        return;
      }
      setResult(payload as ScenarioRunResponse);
      router.refresh(); // picks up the new row in the "recent scenario runs" list below, once revalidated
    } catch {
      setError("could not reach the simulation API");
    } finally {
      setSubmitting(false);
    }
  }

  if (scenarioTypes.length === 0) {
    return (
      <div className="panel p-6 text-sm text-slate-500 dark:text-slate-400">
        No scenario types available - is api/simulation_api.py mounted and reachable?
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="panel space-y-4 p-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="space-y-1">
          <label htmlFor="scenario-type" className="text-sm font-medium text-slate-700 dark:text-slate-300">
            Scenario type
          </label>
          <select
            id="scenario-type"
            value={scenarioType}
            onChange={(event) => handleScenarioTypeChange(event.target.value)}
            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
          >
            {scenarioTypes.map((type) => (
              <option key={type} value={type}>
                {type.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <label htmlFor="ticks" className="text-sm font-medium text-slate-700 dark:text-slate-300">
            Ticks
          </label>
          <input
            id="ticks"
            type="number"
            min={1}
            value={ticks}
            onChange={(event) => setTicks(Number(event.target.value))}
            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
          />
        </div>
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

      {Object.keys(paramSchema).length > 0 ? (
        <div className="space-y-1">
          <p className="text-sm font-medium text-slate-700 dark:text-slate-300">Scenario params</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {Object.entries(paramSchema).map(([key, hint]) => (
              <div key={key} className="space-y-1">
                <label htmlFor={`param-${key}`} className="text-xs text-slate-500">
                  {key} <span className="text-slate-400">({hint})</span>
                </label>
                <input
                  id={`param-${key}`}
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

      <details className="rounded-md border border-slate-200 p-3 text-sm dark:border-slate-800">
        <summary className="cursor-pointer font-medium text-slate-700 dark:text-slate-300">
          Agent strategy overrides (optional, this run only)
        </summary>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <label htmlFor="retailer-override" className="text-xs text-slate-500">
              Retailer to override
            </label>
            <select
              id="retailer-override"
              value={retailerOverrideId}
              onChange={(event) => setRetailerOverrideId(event.target.value)}
              className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
            >
              <option value="">(none)</option>
              {(agents.retailer_ids ?? []).map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
            {retailerOverrideId ? (
              <div className="space-y-2 pl-2">
                <select
                  value={promotionStrategy}
                  onChange={(event) => setPromotionStrategy(event.target.value)}
                  className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
                >
                  <option value="">promotion_strategy: default ({agents.default_retailer_strategy?.promotion_strategy})</option>
                  <option value="none">none</option>
                  <option value="periodic">periodic</option>
                  <option value="anomaly_triggered">anomaly_triggered</option>
                </select>
                <input
                  type="number"
                  step="0.01"
                  placeholder={`promotion_discount: default ${agents.default_retailer_strategy?.promotion_discount}`}
                  value={promotionDiscount}
                  onChange={(event) => setPromotionDiscount(event.target.value)}
                  className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
                />
                <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
                  <input type="checkbox" checked={mlDriven} onChange={(event) => setMlDriven(event.target.checked)} />
                  ml_driven (nudge price off ml.forecasts)
                </label>
              </div>
            ) : null}
          </div>
          <div className="space-y-2">
            <label htmlFor="product-override" className="text-xs text-slate-500">
              Product to override
            </label>
            <select
              id="product-override"
              value={productOverrideId}
              onChange={(event) => setProductOverrideId(event.target.value)}
              className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
            >
              <option value="">(none)</option>
              {(agents.product_ids ?? []).map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
            {productOverrideId ? (
              <input
                type="number"
                step="0.1"
                placeholder={`price_elasticity: default ${agents.default_product_strategy?.price_elasticity}`}
                value={priceElasticity}
                onChange={(event) => setPriceElasticity(event.target.value)}
                className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
              />
            ) : null}
          </div>
        </div>
      </details>

      {error ? <p className="text-sm text-coral">{error}</p> : null}

      <button
        type="submit"
        disabled={submitting}
        className="h-9 rounded-md bg-plum px-4 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-60"
      >
        {submitting ? "Running…" : "Run scenario"}
      </button>

      {result ? (
        <div className="space-y-3 rounded-md border border-plum/30 bg-plum/5 p-4 dark:border-plum/30 dark:bg-plum/10">
          <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
            {result.scenario_type.replace(/_/g, " ")} - baseline vs. scenario over the same seeded ticks
          </p>
          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <p className="text-xs text-slate-500">Baseline GMV</p>
              <p className="text-lg font-semibold text-slate-950 dark:text-white">{money(result.predicted_gmv_baseline)}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Scenario GMV</p>
              <p className="text-lg font-semibold text-slate-950 dark:text-white">{money(result.predicted_gmv_scenario)}</p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Delta</p>
              <p className={`text-lg font-semibold ${result.predicted_gmv_delta >= 0 ? "text-mint" : "text-coral"}`}>
                {result.predicted_gmv_delta >= 0 ? "+" : ""}
                {money(result.predicted_gmv_delta)}
              </p>
            </div>
          </div>
          {result.predicted_retailer_health.length > 0 ? (
            <div>
              <p className="mb-1 text-xs font-medium text-slate-500">Post-scenario retailer health</p>
              <ul className="space-y-1 text-xs text-slate-600 dark:text-slate-300">
                {result.predicted_retailer_health.slice(0, 5).map((r) => (
                  <li key={r.retailer_id}>
                    {r.retailer_id}: {number(r.order_count)} order(s), {money(r.net_revenue)}, health {number(r.retailer_health_score, 1)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {result.predicted_recommendations.length > 0 ? (
            <div>
              <p className="mb-1 text-xs font-medium text-slate-500">Worth promoting after this scenario</p>
              <ul className="space-y-1 text-xs text-slate-600 dark:text-slate-300">
                {result.predicted_recommendations.slice(0, 5).map((rec) => (
                  <li key={rec.product_id}>
                    {rec.product_name ?? rec.product_id} (sell-through {number(rec.demand_curve_point, 3)})
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
