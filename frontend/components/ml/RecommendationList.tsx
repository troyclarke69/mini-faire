import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";
import { api, number } from "@/lib/api";
import type { Recommendation } from "@/lib/types";

// Async Server Component fetching its own data, same convention
// components/monitoring/AnomalyTable.tsx and AlertsFeed.tsx use. Groups
// ml.recommendations rows by (recommendation_type, source_entity_id) and
// renders each group's ranked recommendation edges together - the raw table
// is one row per edge (see ml/models/recommendations.py's Recommendation
// dataclass), which reads far better grouped than as a flat table.

const TYPE_LABEL: Record<string, string> = {
  product_similar: "Products similar to",
  retailer_similar: "Retailers similar to",
  product_frequently_bought_together: "Ordered alongside",
  product_trending: "Trending in category",
  retailer_growth: "Retailers likely to grow"
};

export async function RecommendationList({
  recommendationType,
  limit
}: { recommendationType?: string; limit?: number } = {}) {
  const rows = await api.recommendations();
  const filtered = recommendationType ? rows.filter((row) => row.recommendation_type === recommendationType) : rows;

  const groups = new Map<string, Recommendation[]>();
  for (const row of filtered) {
    const key = `${row.recommendation_type}::${row.source_entity_id}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(row);
  }
  for (const list of groups.values()) list.sort((a, b) => a.rank - b.rank);

  const groupEntries = Array.from(groups.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  const visible = limit ? groupEntries.slice(0, limit) : groupEntries;

  return (
    <TablePanel title="Recommendations" actions={<span className="text-xs text-slate-500">{filtered.length} total</span>}>
      {visible.length === 0 ? (
        <EmptyState label="No recommendations generated yet - run `python orchestration/ml_training_flow.py` first." />
      ) : (
        <div className="divide-y divide-slate-200 dark:divide-slate-800">
          {visible.map(([key, recs]) => {
            const first = recs[0];
            return (
              <article key={key} className="p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium uppercase tracking-wide text-plum">
                    {TYPE_LABEL[first.recommendation_type] ?? first.recommendation_type.replace(/_/g, " ")}
                  </span>
                  <span className="text-sm font-semibold text-slate-950 dark:text-white">{first.source_entity_id}</span>
                  <span className="text-xs text-slate-500">({first.method.replace(/_/g, " ")})</span>
                </div>
                <ol className="flex flex-wrap gap-2">
                  {recs.map((rec) => (
                    <li
                      key={rec.recommendation_id}
                      className="rounded-md border border-slate-200 px-2 py-1 text-xs dark:border-slate-800"
                    >
                      #{rec.rank} {rec.recommended_entity_id}{" "}
                      <span className="text-slate-500">({number(rec.score, 3)})</span>
                    </li>
                  ))}
                </ol>
              </article>
            );
          })}
        </div>
      )}
    </TablePanel>
  );
}
