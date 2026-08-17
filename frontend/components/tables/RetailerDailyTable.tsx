import { api, money, number } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";

export async function RetailerDailyTable() {
  const rows = await api.retailerDaily();

  return (
    <TablePanel title="Retailer Daily Metrics">
      {rows.length === 0 ? (
        <EmptyState />
      ) : (
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {["Date", "Retailer", "Orders", "Units", "GMV", "Net Revenue", "AOV"].map((heading) => (
                <th key={heading} className="px-3 py-2">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.order_date}-${row.retailer_id}`}>
                <td className="table-cell">{row.order_date}</td>
                <td className="table-cell font-medium">{row.retailer_name}</td>
                <td className="table-cell">{number(row.order_count)}</td>
                <td className="table-cell">{number(row.units_sold)}</td>
                <td className="table-cell">{money(row.gmv)}</td>
                <td className="table-cell">{money(row.net_revenue)}</td>
                <td className="table-cell">{money(row.average_order_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </TablePanel>
  );
}

