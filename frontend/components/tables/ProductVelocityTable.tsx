import { api, number, percent } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";

export async function ProductVelocityTable() {
  const rows = await api.productVelocity();

  return (
    <TablePanel title="Product Velocity">
      {rows.length === 0 ? (
        <EmptyState />
      ) : (
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {["Product", "Category", "Orders", "Units", "Inventory", "Velocity"].map((heading) => (
                <th key={heading} className="px-3 py-2">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.product_id}>
                <td className="table-cell font-medium">{row.product_name}</td>
                <td className="table-cell">{row.product_category}</td>
                <td className="table-cell">{number(row.order_count)}</td>
                <td className="table-cell">{number(row.units_sold)}</td>
                <td className="table-cell">{number(row.inventory_count)}</td>
                <td className="table-cell">{percent(row.inventory_velocity)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </TablePanel>
  );
}

