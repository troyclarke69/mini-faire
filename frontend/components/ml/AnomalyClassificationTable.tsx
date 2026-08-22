import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";
import { api, percent } from "@/lib/api";
import type { AnomalyClassification } from "@/lib/types";

// Same table convention as components/monitoring/AnomalyTable.tsx, one row
// per ml.anomaly_classifications entry. `confidence` is NOT "how sure are we
// this is really an anomaly" (Phase 5's rule-based detectors already decided
// that) - it's "how distinguishable is this predicted anomaly_type from the
// others given only its numeric signature" - see ml/models/
// anomaly_classifier.py's module docstring. The agrees/disagrees badge
// compares the classifier's predicted_type against the rule-based
// detector's original actual_type; a disagreement is worth a second look,
// not necessarily a classifier error.

function agreementBadgeClasses(agrees: boolean): string {
  return agrees
    ? "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium bg-mint/10 text-mint border-mint/30"
    : "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium bg-marigold/10 text-marigold border-marigold/30";
}

export async function AnomalyClassificationTable({ limit }: { limit?: number } = {}) {
  const rows = await api.anomalyClassifications();
  const visible = limit ? rows.slice(0, limit) : rows;

  return (
    <TablePanel
      title="Anomaly Classifications"
      actions={<span className="text-xs text-slate-500">{rows.length} total</span>}
    >
      {visible.length === 0 ? (
        <EmptyState label="No anomalies classified yet - run `python orchestration/ml_training_flow.py` first." />
      ) : (
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {["Predicted Type", "Detector Type", "Agreement", "Confidence", "Anomaly", "Model v", "Classified"].map(
                (heading) => (
                  <th key={heading} className="px-3 py-2">
                    {heading}
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody>
            {visible.map((row: AnomalyClassification) => (
              <tr key={row.classification_id}>
                <td className="table-cell font-medium">{row.predicted_type.replace(/_/g, " ")}</td>
                <td className="table-cell">{row.actual_type.replace(/_/g, " ")}</td>
                <td className="table-cell">
                  <span className={agreementBadgeClasses(row.agrees_with_detector)}>
                    {row.agrees_with_detector ? "agrees" : "disagrees"}
                  </span>
                </td>
                <td className="table-cell">{percent(row.confidence)}</td>
                <td className="table-cell max-w-48 truncate text-xs text-slate-500" title={row.anomaly_id}>
                  {row.anomaly_id}
                </td>
                <td className="table-cell">{row.model_version ?? "n/a"}</td>
                <td className="table-cell text-xs text-slate-500" title={row.classified_at}>
                  {row.classified_at}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </TablePanel>
  );
}
