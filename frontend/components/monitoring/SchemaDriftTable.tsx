import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";
import { api, severityBadgeClasses } from "@/lib/api";
import type { SchemaDriftEvent } from "@/lib/types";

export async function SchemaDriftTable({ limit }: { limit?: number } = {}) {
  const rows = await api.schemaDrift();
  const visible = limit ? rows.slice(0, limit) : rows;

  return (
    <TablePanel title="Schema Drift" actions={<span className="text-xs text-slate-500">{rows.length} total</span>}>
      {visible.length === 0 ? (
        <EmptyState label="No schema drift detected yet." />
      ) : (
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {["Severity", "Entity", "Drift Type", "Field", "Expected", "Actual", "Run", "Detected"].map((heading) => (
                <th key={heading} className="px-3 py-2">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row: SchemaDriftEvent) => (
              <tr key={row.drift_id}>
                <td className="table-cell">
                  <span className={severityBadgeClasses(row.severity)}>{row.severity}</span>
                </td>
                <td className="table-cell font-medium">{row.entity}</td>
                <td className="table-cell">{row.drift_type.replace(/_/g, " ")}</td>
                <td className="table-cell">{row.field_name}</td>
                <td className="table-cell max-w-40 truncate" title={row.expected}>
                  {row.expected}
                </td>
                <td className="table-cell max-w-40 truncate" title={row.actual}>
                  {row.actual}
                </td>
                <td className="table-cell max-w-48 truncate text-xs text-slate-500" title={row.run_id}>
                  {row.run_id}
                </td>
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
