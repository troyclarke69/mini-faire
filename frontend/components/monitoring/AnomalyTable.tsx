import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";
import { api, number, severityBadgeClasses } from "@/lib/api";
import type { AnomalyEvent } from "@/lib/types";

function methodLabel(method: string): string {
  return method.replace(/_/g, " ");
}

export async function AnomalyTable({ limit }: { limit?: number } = {}) {
  const rows = await api.anomalies();
  const visible = limit ? rows.slice(0, limit) : rows;

  return (
    <TablePanel title="Anomalies" actions={<span className="text-xs text-slate-500">{rows.length} total</span>}>
      {visible.length === 0 ? (
        <EmptyState label="No anomalies detected yet." />
      ) : (
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {["Severity", "Type", "Entity", "Metric", "Value", "Baseline", "Method", "Detected"].map((heading) => (
                <th key={heading} className="px-3 py-2">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row: AnomalyEvent) => (
              <tr key={row.anomaly_id}>
                <td className="table-cell">
                  <span className={severityBadgeClasses(row.severity)}>{row.severity}</span>
                </td>
                <td className="table-cell font-medium">{row.anomaly_type.replace(/_/g, " ")}</td>
                <td className="table-cell">
                  {row.entity_type}={row.entity_id}
                </td>
                <td className="table-cell">{row.metric_name}</td>
                <td className="table-cell">{number(row.metric_value, 2)}</td>
                <td className="table-cell">{row.baseline_value === null ? "n/a" : number(row.baseline_value, 2)}</td>
                <td className="table-cell text-xs text-slate-500">{methodLabel(row.method)}</td>
                <td className="table-cell text-xs text-slate-500" title={row.detected_at}>
                  {row.detected_at}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </TablePanel>
  );
}
