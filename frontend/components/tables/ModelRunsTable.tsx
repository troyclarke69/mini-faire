import { api, number } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";

export async function ModelRunsTable() {
  const rows = await api.eltModelRuns();

  return (
    <TablePanel title="ELT Model Runs">
      {rows.length === 0 ? (
        <EmptyState />
      ) : (
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {["Model", "Strategy", "Business Key", "Affected", "Target Rows", "Watermark", "Status"].map((heading) => (
                <th key={heading} className="px-3 py-2">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.model_name}-${row.completed_at}-${index}`}>
                <td className="table-cell font-medium">{row.model_name}</td>
                <td className="table-cell">{row.load_strategy}</td>
                <td className="table-cell">{row.business_key}</td>
                <td className="table-cell">{number(row.affected_key_count)}</td>
                <td className="table-cell">{number(row.target_row_count)}</td>
                <td className="table-cell">{row.high_watermark ?? "n/a"}</td>
                <td className="table-cell">{row.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </TablePanel>
  );
}

