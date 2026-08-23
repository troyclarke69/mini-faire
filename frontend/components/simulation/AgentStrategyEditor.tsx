import Link from "next/link";
import { TablePanel } from "@/components/TablePanel";
import type { AgentStrategy } from "@/lib/types";

// PHASE8-SIMULATION.md Section 7's AgentStrategyEditor. NOT an editor of
// persisted state - see lib/types.ts's AgentStrategy comment and
// simulation/scenario_engine.py's build_agents() docstring: agent objects
// are built fresh, in-memory, per run, and this repo has no "saved
// strategy" table for them. This component is the read-only reference view
// of every default field's current value plus the twin's retailer/product
// id set; components/simulation/ScenarioBuilder.tsx's "Agent strategy
// overrides" section is where a value actually gets changed, scoped to one
// run at a time - this page links there rather than duplicating an editable
// form for state that has nowhere durable to be saved.

function fieldRows(obj: Record<string, unknown>): { key: string; value: string }[] {
  return Object.entries(obj).map(([key, value]) => ({
    key,
    value: Array.isArray(value) ? `[${value.join(", ")}]` : String(value)
  }));
}

function StrategyTable({ title, fields }: { title: string; fields: { key: string; value: string }[] }) {
  return (
    <TablePanel title={title}>
      <table className="min-w-full">
        <thead className="table-head">
          <tr>
            <th className="px-3 py-2">Field</th>
            <th className="px-3 py-2">Default</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((row) => (
            <tr key={row.key}>
              <td className="table-cell font-mono text-xs">{row.key}</td>
              <td className="table-cell text-xs text-slate-600 dark:text-slate-300">{row.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TablePanel>
  );
}

export function AgentStrategyEditor({ agents }: { agents: AgentStrategy }) {
  const retailerIds = agents.retailer_ids ?? [];
  const productIds = agents.product_ids ?? [];

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600 dark:text-slate-400">
        {retailerIds.length} retailer agent(s) and {productIds.length} product agent(s) would be built from the current
        digital twin, one marketplace agent overall. Strategies below are the defaults every agent starts from; override
        one retailer or product&apos;s strategy for a single run from the{" "}
        <Link href="/simulation/scenarios" className="font-medium text-plum">
          Scenarios
        </Link>{" "}
        tab.
      </p>
      <div className="grid gap-4 lg:grid-cols-3">
        <StrategyTable title="Marketplace Strategy (default)" fields={fieldRows(agents.default_marketplace_strategy ?? {})} />
        <StrategyTable title="Retailer Strategy (default)" fields={fieldRows(agents.default_retailer_strategy ?? {})} />
        <StrategyTable title="Product Strategy (default)" fields={fieldRows(agents.default_product_strategy ?? {})} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <TablePanel title="Retailer IDs in this twin" actions={<span className="text-xs text-slate-500">{retailerIds.length} total</span>}>
          <div className="flex flex-wrap gap-2 p-4">
            {retailerIds.map((id) => (
              <span key={id} className="rounded-md border border-slate-200 px-2 py-1 text-xs dark:border-slate-800">
                {id}
              </span>
            ))}
          </div>
        </TablePanel>
        <TablePanel title="Product IDs in this twin" actions={<span className="text-xs text-slate-500">{productIds.length} total</span>}>
          <div className="flex flex-wrap gap-2 p-4">
            {productIds.map((id) => (
              <span key={id} className="rounded-md border border-slate-200 px-2 py-1 text-xs dark:border-slate-800">
                {id}
              </span>
            ))}
          </div>
        </TablePanel>
      </div>
    </div>
  );
}
