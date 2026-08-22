"use client";

import type { Forecast } from "@/lib/types";
import { number } from "@/lib/api";

// Renders one forecast series (already filtered by the caller to a single
// (forecast_type, entity_id) pair - see app/ml/forecasts/page.tsx) as an SVG
// line with a shaded confidence band (lower_bound..upper_bound - see
// ml/models/forecasting.py's module docstring on why that band is an
// empirical +/-1.28*residual_std approximation, not a rigorous prediction
// interval).
//
// PHASE6-ML.md Section 9 describes this component as plotting "actual vs
// forecast", but ml.forecasts only ever persists the *predicted* curve
// (ml/models/forecasting.py's forecast_series() forecasts forward from
// warehouse history, it does not also write the historical actuals it
// forecasted from back into ml.forecasts) - and which warehouse table holds
// the matching actuals differs per forecast_type (marts.metrics_retailer_daily
// for gmv_*, marts.fact_orders for velocity_*, fact_product_events for
// inventory_level/price_trend). Joining each forecast_type against its own
// actuals table here would need type-specific fetch logic this component
// doesn't have visibility into. Adapted to plotting the forecast curve
// itself (point estimate + confidence band) rather than overlaying actuals -
// documented here the same way ml/models/recommendations.py documents its
// "frequently bought together" adaptation.
//
// Self-contained SVG (no charting library), same house style as
// components/charts/SimpleBarChart.tsx.

const WIDTH = 640;
const HEIGHT = 220;
const MARGIN = { top: 16, right: 16, bottom: 28, left: 56 };

function shortDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString([], { month: "short", day: "numeric" });
}

export function ForecastChart({ title, series }: { title: string; series: Forecast[] }) {
  const ordered = [...series].sort((a, b) => a.target_date.localeCompare(b.target_date));

  if (ordered.length === 0) {
    return <div className="panel p-6 text-sm text-slate-500 dark:text-slate-400">No forecast data for this series yet.</div>;
  }

  const plotWidth = WIDTH - MARGIN.left - MARGIN.right;
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const n = ordered.length;

  const allValues = ordered.flatMap((row) => [row.lower_bound, row.forecast_value, row.upper_bound]);
  const min = Math.min(...allValues);
  const max = Math.max(...allValues);
  const span = max - min || 1;

  const x = (i: number) => MARGIN.left + (n === 1 ? plotWidth / 2 : (i / (n - 1)) * plotWidth);
  const y = (value: number) => MARGIN.top + (1 - (value - min) / span) * plotHeight;

  const linePoints = ordered.map((row, i) => `${x(i)},${y(row.forecast_value)}`).join(" ");
  const bandPoints =
    ordered.map((row, i) => `${x(i)},${y(row.upper_bound)}`).join(" ") +
    " " +
    [...ordered]
      .map((row, i) => `${x(i)},${y(row.lower_bound)}`)
      .reverse()
      .join(" ");

  const method = ordered[0]?.metadata ? safeParseMethod(ordered[0].metadata) : null;

  return (
    <section className="panel p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-950 dark:text-white">{title}</h2>
        {method ? <span className="text-xs text-slate-500">method: {method}</span> : null}
      </div>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" role="img" aria-label={`${title} forecast chart`}>
        <polygon points={bandPoints} className="fill-mint/15" />
        <polyline points={linePoints} fill="none" stroke="#2e7d72" strokeWidth={2} />
        {ordered.map((row, i) => (
          <circle key={row.forecast_id} cx={x(i)} cy={y(row.forecast_value)} r={2.5} fill="#2e7d72" />
        ))}
        <text x={MARGIN.left} y={HEIGHT - 6} className="fill-slate-500 text-[10px]">
          {shortDate(ordered[0].target_date)}
        </text>
        <text x={WIDTH - MARGIN.right} y={HEIGHT - 6} textAnchor="end" className="fill-slate-500 text-[10px]">
          {shortDate(ordered[n - 1].target_date)}
        </text>
        <text x={MARGIN.left - 6} y={MARGIN.top + 4} textAnchor="end" className="fill-slate-500 text-[10px]">
          {number(max, 1)}
        </text>
        <text x={MARGIN.left - 6} y={HEIGHT - MARGIN.bottom} textAnchor="end" className="fill-slate-500 text-[10px]">
          {number(min, 1)}
        </text>
      </svg>
    </section>
  );
}

function safeParseMethod(metadataJson: string): string | null {
  try {
    const parsed = JSON.parse(metadataJson);
    return typeof parsed === "object" && parsed !== null && typeof parsed.method === "string"
      ? parsed.method.replace(/_/g, " ")
      : null;
  } catch {
    return null;
  }
}
