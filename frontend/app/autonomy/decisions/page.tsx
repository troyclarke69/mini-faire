import Link from "next/link";
import { AgentDecisionTable } from "@/components/autonomy/AgentDecisionTable";
import { AutonomyLiveBar } from "@/components/autonomy/AutonomyLiveBar";
import { AutonomyTabs } from "@/components/autonomy/AutonomyTabs";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";

const AGENT_TYPES = ["pricing", "inventory", "demand", "anomaly_response", "retailer_strategy"] as const;

const FETCHER_BY_AGENT_TYPE: Record<(typeof AGENT_TYPES)[number], typeof api.autonomyPricing> = {
  pricing: api.autonomyPricing,
  inventory: api.autonomyInventory,
  demand: api.autonomyDemand,
  anomaly_response: api.autonomyAnomalies,
  retailer_strategy: api.autonomyRetailerStrategy
};

export default async function AutonomyDecisionsPage({
  searchParams
}: {
  searchParams: { agent_type?: string };
}) {
  const agentType = typeof searchParams.agent_type === "string" ? searchParams.agent_type : undefined;
  const fetcher =
    agentType && (AGENT_TYPES as readonly string[]).includes(agentType)
      ? FETCHER_BY_AGENT_TYPE[agentType as (typeof AGENT_TYPES)[number]]
      : undefined;

  const feed = fetcher ? await fetcher(undefined, 100) : await api.autonomyActions(undefined, 100);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Decisions"
        subtitle="Every proposed agent action (autonomy.pricing_actions / inventory_actions / demand_actions / anomaly_actions / retailer_strategy_actions), applied or rejected - the full audit trail api/autonomy_api.py's GET /autonomy/actions merges and sorts."
      />
      <AutonomyTabs />
      <AutonomyLiveBar />
      <nav className="flex flex-wrap gap-2 text-xs">
        <Link
          href="/autonomy/decisions"
          className={`rounded-full border px-3 py-1 font-medium ${
            !fetcher ? "border-marigold/30 bg-marigold/10 text-marigold" : "border-slate-300 text-slate-500 dark:border-slate-700"
          }`}
        >
          all
        </Link>
        {AGENT_TYPES.map((type) => (
          <Link
            key={type}
            href={`/autonomy/decisions?agent_type=${type}`}
            className={`rounded-full border px-3 py-1 font-medium ${
              agentType === type
                ? "border-marigold/30 bg-marigold/10 text-marigold"
                : "border-slate-300 text-slate-500 dark:border-slate-700"
            }`}
          >
            {type.replace(/_/g, " ")}
          </Link>
        ))}
      </nav>
      <AgentDecisionTable actions={feed.actions ?? []} showAgentType={!fetcher} />
    </div>
  );
}
