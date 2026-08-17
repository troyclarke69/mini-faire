import { PageHeader } from "@/components/PageHeader";
import { TablePanel } from "@/components/TablePanel";
import { ModelRunsTable } from "@/components/tables/ModelRunsTable";
import { api, number } from "@/lib/api";

export default async function ModelRunsPage() {
  const computeRuns = await api.computeModelRuns();

  return (
    <div className="space-y-6">
      <PageHeader title="Model Runs" subtitle="ELT model history and Polars compute run history." />
      <ModelRunsTable />
      <TablePanel title="Polars Compute Runs">
        <table className="min-w-full">
          <thead className="table-head">
            <tr>{["Model", "Target", "Sources", "Rows", "Columns", "Computed", "Status"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr>
          </thead>
          <tbody>
            {computeRuns.map((row, index) => (
              <tr key={`${row.model_name}-${row.computed_at}-${index}`}>
                <td className="table-cell font-medium">{row.model_name}</td>
                <td className="table-cell">{row.target_table}</td>
                <td className="table-cell">{row.source_tables}</td>
                <td className="table-cell">{number(row.row_count)}</td>
                <td className="table-cell">{number(row.column_count)}</td>
                <td className="table-cell">{row.computed_at}</td>
                <td className="table-cell">{row.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TablePanel>
    </div>
  );
}

