import { api, number } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";

export async function IngestionRunsTable() {
  const rows = await api.ingestionRuns();

  return (
    <TablePanel title="Ingestion Runs">
      {rows.length === 0 ? (
        <EmptyState />
      ) : (
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {["Run", "Source", "Entity", "Valid", "Invalid", "Duration", "Contract", "Status"].map((heading) => (
                <th key={heading} className="px-3 py-2">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.run_id}>
                <td className="table-cell max-w-64 truncate font-medium" title={row.run_id}>
                  {row.run_id}
                </td>
                <td className="table-cell">{row.source}</td>
                <td className="table-cell">{row.entity}</td>
                <td className="table-cell">{number(row.valid_count)}</td>
                <td className="table-cell">{number(row.invalid_count)}</td>
                <td className="table-cell">{number(row.duration_ms)} ms</td>
                <td className="table-cell">{row.contract_name}</td>
                <td className="table-cell">{row.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </TablePanel>
  );
}

