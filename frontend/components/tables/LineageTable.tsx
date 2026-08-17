import { api } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";

export async function LineageTable() {
  const rows = await api.lineageEdges();

  return (
    <TablePanel title="Lineage Edges">
      {rows.length === 0 ? (
        <EmptyState />
      ) : (
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {["Entity", "Edge Type", "Source", "Target", "Run"].map((heading) => (
                <th key={heading} className="px-3 py-2">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.run_id}-${index}`}>
                <td className="table-cell">{row.entity}</td>
                <td className="table-cell">{row.edge_type}</td>
                <td className="table-cell max-w-80 truncate" title={row.source_node}>
                  {row.source_node}
                </td>
                <td className="table-cell max-w-80 truncate" title={row.target_node}>
                  {row.target_node}
                </td>
                <td className="table-cell max-w-64 truncate" title={row.run_id}>
                  {row.run_id}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </TablePanel>
  );
}

