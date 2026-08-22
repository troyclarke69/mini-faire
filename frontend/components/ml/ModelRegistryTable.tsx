import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";
import { api } from "@/lib/api";
import type { ModelMetadata, ModelStatus } from "@/lib/types";

// Every registered version of every model_name (see ml/registry.py's
// module docstring: model_name is the stable identity across retrains,
// version auto-increments per model_name) - this is meant to show the full
// train/promote/rollback history, not just what's currently live, so a
// reader can see e.g. "v2 scored worse and was never promoted" or "v4 was
// promoted, failed its post-activation sanity check, and got rolled back to
// v3" as distinct rows with distinct statuses.

const STATUS_CLASSES: Record<ModelStatus, string> = {
  active: "bg-mint/10 text-mint border-mint/30",
  inactive:
    "bg-slate-100 text-slate-600 border-slate-300 dark:bg-slate-900 dark:text-slate-300 dark:border-slate-700",
  superseded: "bg-marigold/10 text-marigold border-marigold/30",
  rolled_back: "bg-coral/10 text-coral border-coral/30"
};

function statusBadge(status: string) {
  const classes = STATUS_CLASSES[status as ModelStatus] ?? STATUS_CLASSES.inactive;
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${classes}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

function summarizeMetrics(raw: string): string {
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return "n/a";
    const entries = Object.entries(parsed as Record<string, unknown>).filter(
      ([, value]) => value !== null && value !== undefined
    );
    if (entries.length === 0) return "n/a";
    return entries
      .map(([key, value]) => `${key}=${typeof value === "number" ? value.toFixed(3) : String(value)}`)
      .join(", ");
  } catch {
    return "n/a";
  }
}

export async function ModelRegistryTable() {
  const rows = await api.mlModels();

  return (
    <TablePanel title="Model Registry" actions={<span className="text-xs text-slate-500">{rows.length} version(s)</span>}>
      {rows.length === 0 ? (
        <EmptyState label="No models registered yet - run `python orchestration/ml_training_flow.py` first." />
      ) : (
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {["Model", "Type", "Version", "Status", "Metrics", "Trained", "Artifact"].map((heading) => (
                <th key={heading} className="px-3 py-2">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row: ModelMetadata) => (
              <tr key={row.model_id}>
                <td className="table-cell font-medium">{row.model_name}</td>
                <td className="table-cell">{row.model_type.replace(/_/g, " ")}</td>
                <td className="table-cell">v{row.version}</td>
                <td className="table-cell">{statusBadge(row.status)}</td>
                <td
                  className="table-cell max-w-64 truncate text-xs text-slate-500"
                  title={summarizeMetrics(row.metrics)}
                >
                  {summarizeMetrics(row.metrics)}
                </td>
                <td className="table-cell text-xs text-slate-500" title={row.trained_at}>
                  {row.trained_at}
                </td>
                <td className="table-cell text-xs text-slate-500">
                  {row.artifact_path ? "persisted" : "refit at inference"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </TablePanel>
  );
}
